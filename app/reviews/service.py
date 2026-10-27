"""Storefront posting and deterministic, provider-independent text moderation."""

from __future__ import annotations

import math
import re
import unicodedata
from datetime import timedelta

from flask import current_app, request
from sqlalchemy import func, select
from werkzeug.datastructures import FileStorage

from app import ratelimit
from app.accounts.models import User
from app.catalog.models import Product, Variant, utcnow
from app.extensions import db
from app.orders.models import Order, OrderLine, PaymentStatus
from app.reviews import images
from app.reviews.models import (
    ModerationRemoval,
    ModerationWord,
    PostKind,
    PostStatus,
    ProductPost,
    ProductPostPhoto,
)

LINK_RE = re.compile(r"(?:https?://|www\.|\b[\w-]+\.(?:com|net|org|io)\b)", re.I)
LEET = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a"})

# These small built-ins are a baseline, not a claim of complete linguistic moderation.
OBSCENE = {
    "en": {"fuck", "shit", "bitch", "cunt", "porn", "nude", "naked"},
    "fr": {"merde", "putain", "porno", "nu"},
    "es": {"mierda", "puta", "porno", "desnudo"},
    "pt": {"merda", "puta", "porno", "nu"},
    "sw": {"uchi", "tupu"},
    "yo": {"ihoho"},
    "ig": set(),
    "ha": set(),
    "tw": set(),
}
TOPIC = {
    "en": {
        "fabric",
        "print",
        "color",
        "pattern",
        "quality",
        "cotton",
        "wax",
        "stitch",
        "size",
        "fit",
        "length",
        "yard",
        "bundle",
        "delivery",
        "shipping",
        "package",
        "price",
        "value",
        "gift",
        "sew",
        "dress",
        "wrap",
        "skirt",
        "cushion",
        "love",
        "beautiful",
        "recommend",
        "soft",
        "photo",
        "product",
        "material",
    },
    "fr": {
        "tissu",
        "imprime",
        "couleur",
        "motif",
        "qualite",
        "coton",
        "taille",
        "livraison",
        "prix",
        "robe",
        "beau",
        "recommande",
    },
    "es": {
        "tela",
        "estampado",
        "color",
        "patron",
        "calidad",
        "algodon",
        "talla",
        "envio",
        "precio",
        "vestido",
        "bonito",
        "recomiendo",
    },
    "pt": {
        "tecido",
        "estampa",
        "cor",
        "padrao",
        "qualidade",
        "algodao",
        "tamanho",
        "entrega",
        "preco",
        "vestido",
        "bonito",
        "recomendo",
    },
    "sw": {"kitambaa", "rangi", "muundo", "ubora", "pamba", "ukubwa", "bei", "nguo", "nzuri"},
    "yo": {"aso", "awo", "didara", "owu", "iwon", "owo", "ewa"},
    "ig": {"akwa", "agba", "mma", "onuahia"},
    "ha": {"yadi", "launi", "inganci", "auduga", "girman", "farashi", "kyau"},
    "tw": {"ntoma", "ahosu", "bo", "fɛ"},
}
LANGUAGE_MARKERS = {
    "en": {"the", "this", "that", "with", "for", "and", "was", "were"},
    "fr": {"le", "la", "les", "une", "est", "avec", "pour"},
    "es": {"el", "la", "los", "una", "con", "para", "muy"},
    "pt": {"o", "a", "os", "uma", "com", "para", "muito"},
    "sw": {"na", "ya", "kwa", "sana", "hii"},
    "yo": {"ati", "fun", "pẹlu", "gan"},
    "ig": {"na", "nke", "maka", "nkeoma"},
    "ha": {"da", "na", "don", "sosai"},
    "tw": {"ne", "na", "paa"},
}


class ReviewError(ValueError):
    pass


def normalize(text: str) -> list[str]:
    folded = unicodedata.normalize("NFKD", text.casefold().translate(LEET))
    plain = "".join(char for char in folded if not unicodedata.combining(char))
    # Three or more repeats are commonly used to evade word filters. Collapse
    # those runs while preserving legitimate doubles such as "cotton".
    plain = re.sub(r"(.)\1{2,}", r"\1", plain)
    return [token for token in re.sub(r"[^a-z\s]", " ", plain).split() if token]


def detect_language(tokens: list[str]) -> str:
    if not tokens:
        return "und"
    scores = {
        language: len(set(tokens).intersection(markers | TOPIC.get(language, set())))
        for language, markers in LANGUAGE_MARKERS.items()
    }
    language, score = max(scores.items(), key=lambda item: item[1])
    # One short marker (notably Romance-language articles such as "a") is too
    # ambiguous to override English.
    if score >= 2:
        return language
    return "en" if all(token.isascii() for token in tokens) else "und"


def contains_obscenity(tokens: list[str], language: str) -> bool:
    configured = set(
        db.session.scalars(
            select(ModerationWord.word).where(
                ModerationWord.language == language,
                ModerationWord.is_topic.is_(False),
                ModerationWord.enabled.is_(True),
            )
        ).all()
    )
    blocked = OBSCENE.get(language, set()) | configured
    joined = "".join(tokens)
    return bool(
        blocked.intersection(tokens) or any(word in joined for word in blocked if len(word) >= 4)
    )


def relevance(tokens: list[str], language: str, product: Product) -> int:
    if language not in TOPIC:
        return 0
    custom = set(
        db.session.scalars(
            select(ModerationWord.word).where(
                ModerationWord.language == language,
                ModerationWord.is_topic.is_(True),
                ModerationWord.enabled.is_(True),
            )
        ).all()
    )
    product_words = set(
        normalize(" ".join([product.name, product.summary, product.description, product.colors]))
    )
    vocabulary = TOPIC[language] | custom | product_words
    matches = sum(1 for token in set(tokens) if token in vocabulary)
    return min(100, round(100 * matches / max(2, math.sqrt(len(tokens)) * 2)))


def create_post(
    user: User,
    product: Product,
    form: dict[str, str],
    photos: list[FileStorage] | None = None,
) -> ProductPost:
    if not ratelimit.allow("product-post-ip", ratelimit.client_key(), 10, 3600):
        raise ReviewError("Too many posts were submitted. Please wait and try again.")
    if not ratelimit.allow("product-post-user", str(user.id), 5, 3600):
        raise ReviewError("Too many posts were submitted. Please wait and try again.")
    retention = current_app.config.get("MODERATION_RETENTION_DAYS")
    if retention:
        cutoff = utcnow() - timedelta(days=90)
        offenses = db.session.scalar(
            select(func.count(ModerationRemoval.id)).where(
                ModerationRemoval.user_id == user.id, ModerationRemoval.created_at >= cutoff
            )
        )
        if int(offenses or 0) >= 3:
            raise ReviewError("Posting is temporarily unavailable for this account.")
    try:
        kind = PostKind(form.get("kind", ""))
    except ValueError:
        raise ReviewError("Choose review or question.") from None
    title = form.get("title", "").strip()
    body = form.get("body", "").strip()
    if not 2 <= len(title) <= 160:
        raise ReviewError("Title must be between 2 and 160 characters.")
    if not 10 <= len(body) <= 3000:
        raise ReviewError("Post must be between 10 and 3,000 characters.")
    if LINK_RE.search(title + " " + body):
        raise ReviewError("Links are not allowed in reviews or questions.")
    duplicate = db.session.scalar(
        select(ProductPost.id).where(
            ProductPost.user_id == user.id,
            ProductPost.product_id == product.id,
            ProductPost.body == body,
        )
    )
    if duplicate:
        raise ReviewError("That post was already submitted.")
    rating = None
    if kind == PostKind.REVIEW:
        try:
            rating = int(form.get("rating", ""))
        except ValueError:
            raise ReviewError("Choose a star rating from 1 to 5.") from None
        if rating not in range(1, 6):
            raise ReviewError("Choose a star rating from 1 to 5.")
    tokens = normalize(title + " " + body)
    language = detect_language(tokens)
    score = relevance(tokens, language, product)
    obscene = contains_obscenity(tokens, language)
    try:
        clean_photos, nude_photo = images.screen(photos or [])
    except images.PhotoError as exc:
        raise ReviewError(str(exc)) from None
    removal_reason = "obscenity" if obscene else ("nude_image" if nude_photo else None)
    removed = removal_reason is not None
    status = (
        PostStatus.REMOVED
        if removed
        else (PostStatus.VISIBLE if score >= 20 else PostStatus.COLLAPSED)
    )
    post = ProductPost(
        product_id=product.id,
        user_id=user.id,
        kind=kind,
        status=status,
        title="" if removed else title,
        body="" if removed else body,
        rating=rating,
        language=language,
        relevance_score=score,
        moderation_reason=removal_reason or ("low_relevance" if score < 20 else None),
        verified_buyer=_verified_buyer(user.id, product.id),
        ip_address=(ratelimit.client_key() or "")[:45] or None,
        user_agent=(request.user_agent.string or "")[:300] or None,
    )
    db.session.add(post)
    db.session.flush()
    if not removed:
        for data in clean_photos:
            db.session.add(
                ProductPostPhoto(
                    post_id=post.id, path=images.save(data), alt=f"Photo: {title}"[:255]
                )
            )
    if removed and retention:  # the text only — a flagged photo is never kept
        db.session.add(
            ModerationRemoval(
                post_id=post.id,
                user_id=user.id,
                reason=removal_reason,
                original_title=title,
                original_body=body,
                expires_at=utcnow() + timedelta(days=int(retention)),
            )
        )
    db.session.commit()
    if removed:
        raise ReviewError("Your post was removed because it broke the posting rules.")
    return post


def public_posts(product: Product) -> list[ProductPost]:
    return list(
        db.session.scalars(
            select(ProductPost)
            .where(
                ProductPost.product_id == product.id,
                ProductPost.status.in_([PostStatus.VISIBLE, PostStatus.COLLAPSED]),
            )
            .order_by(ProductPost.created_at.desc())
        ).all()
    )


def rating_summary(product: Product) -> tuple[float, int]:
    average, count = db.session.execute(
        select(func.avg(ProductPost.rating), func.count(ProductPost.id)).where(
            ProductPost.product_id == product.id,
            ProductPost.kind == PostKind.REVIEW,
            ProductPost.status == PostStatus.VISIBLE,
            ProductPost.rating.is_not(None),
        )
    ).one()
    return round(float(average or 0), 1), int(count or 0)


def purge_expired() -> int:
    expired = db.session.scalars(
        select(ModerationRemoval).where(ModerationRemoval.expires_at <= utcnow())
    ).all()
    for record in expired:
        db.session.delete(record)
    db.session.commit()
    return len(expired)


def _verified_buyer(user_id: int, product_id: int) -> bool:
    return bool(
        db.session.scalar(
            select(OrderLine.id)
            .join(OrderLine.order)
            .join(OrderLine.variant)
            .where(
                Order.user_id == user_id,
                Order.payment_status == PaymentStatus.PAID,
                Variant.product_id == product_id,
            )
        )
    )
