"""Owner approval for changes made by reviewed staff (owner, 2026-10-27).

Staff whose account has `requires_review` (branding, maintenance, sales) can use their Studio
pages normally, but any action that would change the live website is held instead of applied:

1. The submitted form is stored with a plain-language summary of exactly what would change
   (before -> after) and a fingerprint of the item as it is now. An uploaded photo is kept in a
   private folder (never served) until the decision.
2. The owner gets an email with the summary and a link. Approving needs the owner's technical
   admin sign-in (password + authenticator), so a stolen email alone cannot approve anything.
3. On approval the same Studio action is replayed as the staff member, so every check, history
   entry and undo behaves as if they had done it themselves. If the item changed in the meantime
   the fingerprint no longer matches and nothing is overwritten (status "conflict").

Drafts that are not on the website (a new blog draft, editing a draft, planning a sale that is
not confirmed) are not held: they only reach the website through a held action (publish,
confirm sale).
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any, cast

from flask import (
    Flask,
    abort,
    current_app,
    flash,
    g,
    get_flashed_messages,
    redirect,
    request,
    url_for,
)
from sqlalchemy import func, select, update
from werkzeug.datastructures import MultiDict
from werkzeug.exceptions import HTTPException
from werkzeug.utils import secure_filename

from app import mail
from app.catalog.models import Category, Product, ProductImage, Variant, utcnow
from app.catalog.units import format_piece_length, parse_piece_range, parse_width
from app.content.models import BlogPost, BlogRevision, BlogStatus
from app.extensions import db
from app.reviews.models import ModerationWord, ProductPost
from app.studio import permissions
from app.studio.models import (
    ChangeStatus,
    InventoryHistory,
    PriceHistory,
    ProductHistory,
    StudioChange,
    StudioRole,
    StudioSale,
    StudioUser,
)

log = logging.getLogger(__name__)
TIMEZONE_NOTE = "Philadelphia time"


@dataclass
class Described:
    lines: list[str]
    fingerprint: str | None = None
    needs_review: bool = True
    notes: list[str] = field(default_factory=list)


Describer = Callable[[dict[str, Any], MultiDict[str, str]], Described]


def _fp(*parts: Any) -> str:
    return hashlib.sha256(json.dumps(parts, default=str, sort_keys=True).encode()).hexdigest()


def _money(cents: int | None) -> str:
    return "none" if cents is None else f"${cents / 100:,.2f}"


def _clip(text: str, limit: int = 300) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _plain(value: Any) -> str:
    return " ".join(str(value or "").replace("\r\n", "\n").split())


def _variant_label(variant: Variant) -> str:
    option = " / ".join(v.label for v in variant.option_values)
    return f"{variant.product.name}{f' ({option})' if option else ''} [{variant.sku}]"


def _get(model: type[Any], ident: Any) -> Any:
    record = db.session.get(model, int(ident))
    if record is None:
        abort(404)
    return record


# ---------- describers: one per held Studio action ----------


def _inventory_update(args: dict[str, Any], form: MultiDict[str, str]) -> Described:
    variant = _get(Variant, args["variant_id"])
    mode, raw = form.get("mode", "set"), form.get("quantity", "").strip()
    current = variant.stock_quantity
    if mode == "set":
        change = f"{current} → {raw}"
    else:
        try:
            delta = int(raw)
            change = f"{current} {'+' if delta >= 0 else '−'} {abs(delta)} → {current + delta}"
        except ValueError:
            change = f"{current} adjusted by {raw!r}"
    return Described(
        [f"Stock of {_variant_label(variant)}: {change}", f"Reason: {form.get('reason', '')}"],
        _fp("stock", variant.id, current),
    )


def _inventory_undo(args: dict[str, Any], form: MultiDict[str, str]) -> Described:
    history = _get(InventoryHistory, args["history_id"])
    variant = _get(Variant, history.variant_id)
    return Described(
        [
            f"Undo a stock change on {_variant_label(variant)}: "
            f"{history.after_quantity} → back to {history.before_quantity}"
        ],
        _fp("stock-undo", history.id, history.undone_at, variant.stock_quantity),
    )


def _price_update(args: dict[str, Any], form: MultiDict[str, str]) -> Described:
    variant = _get(Variant, args["variant_id"])
    new = form.get("price", "").strip().lstrip("$")
    return Described(
        [
            f"Price of {_variant_label(variant)}: {_money(variant.price_cents)} → ${new}",
            f"Reason: {form.get('reason', '')}",
        ],
        _fp("price", variant.id, variant.price_cents),
    )


def _price_undo(args: dict[str, Any], form: MultiDict[str, str]) -> Described:
    history = _get(PriceHistory, args["history_id"])
    variant = _get(Variant, history.variant_id)
    return Described(
        [
            f"Undo a price change on {_variant_label(variant)}: "
            f"{_money(history.after_cents)} → back to {_money(history.before_cents)}"
        ],
        _fp("price-undo", history.id, history.undone_at, variant.price_cents),
    )


def _price_bulk(args: dict[str, Any], form: MultiDict[str, str]) -> Described:
    from app.studio import service

    try:
        plan = service.plan_bulk_price(form.to_dict())
    except service.StudioError:
        return Described([], needs_review=False)  # the view refuses it with the reason
    changes = plan.changes
    lines = [f"Change {len(changes)} price(s): {plan.summary}", f"Reason: {plan.reason}"]
    lines += [
        f"{_variant_label(row.variant)}: {_money(row.before)} → {_money(row.after)}"
        for row in changes[:60]
    ]
    if len(changes) > 60:
        lines.append(f"…and {len(changes) - 60} more")
    return Described(lines, plan.fingerprint)


def _inventory_bulk(args: dict[str, Any], form: MultiDict[str, str]) -> Described:
    from app.studio import service

    try:
        plan = service.plan_bulk_stock(form.to_dict())
    except service.StudioError:
        return Described([], needs_review=False)  # the view refuses it with the reason
    changes = plan.changes
    lines = [f"Change stock of {len(changes)} option(s): {plan.summary}", f"Reason: {plan.reason}"]
    lines += [f"{_variant_label(row.variant)}: {row.before} → {row.after}" for row in changes[:60]]
    if len(changes) > 60:
        lines.append(f"…and {len(changes) - 60} more")
    return Described(lines, plan.fingerprint)


def _product_bulk(args: dict[str, Any], form: MultiDict[str, str]) -> Described:
    from app.studio import service

    try:
        plan = service.plan_bulk_visibility(form.to_dict())
    except service.StudioError:
        return Described([], needs_review=False)  # the view refuses it with the reason
    changes = plan.changes
    lines = [f"{plan.summary} ({len(changes)} product(s))", f"Reason: {plan.reason}"]
    lines += [row.product.name for row in changes[:60]]
    if len(changes) > 60:
        lines.append(f"…and {len(changes) - 60} more")
    return Described(lines, plan.fingerprint)


def _product_undo(args: dict[str, Any], form: MultiDict[str, str]) -> Described:
    history = _get(ProductHistory, args["history_id"])
    product = _get(Product, history.product_id)
    what = "hide/put back live" if history.kind == "visibility" else "details edit"
    return Described(
        [f"Undo a {what} on {product.name} (change #{history.id})"],
        _fp("product-undo", history.id, history.undone_at, product.status, product.name),
    )


def _product_edit(args: dict[str, Any], form: MultiDict[str, str]) -> Described:
    product = _get(Product, args["product_id"])
    lines = [f"Edit product details: {product.name}"]

    def compare(label: str, before: Any, after: Any, long: bool = False) -> None:
        # Browsers send \r\n; compare text as people read it, not by line-ending bytes.
        if _plain(before) != _plain(after):
            if long:
                lines.append(f"{label} rewritten: “{_clip(str(after), 400)}”")
            else:
                lines.append(f"{label}: “{before or '—'}” → “{after or '—'}”")

    compare("Name", product.name, form.get("name", "").strip())
    compare("Summary", product.summary, form.get("summary", "").strip())
    compare("Description", product.description, form.get("description", "").strip(), long=True)
    if form.get("sale_unit") is not None:
        compare("Sold as", product.sale_unit, form.get("sale_unit"))
    try:
        if form.get("piece_length_yards") is not None:
            compare(
                "Piece length (yards)",
                product.piece_length_text,
                format_piece_length(*parse_piece_range(form.get("piece_length_yards"))),
            )
        if form.get("width_inches") is not None:
            compare(
                "Width (inches)",
                str(product.width_inches or ""),
                str(parse_width(form.get("width_inches")) or ""),
            )
    except ValueError as exc:
        lines.append(f"(Will be refused: {exc})")
    wanted = set(form.getlist("category_ids"))
    current = {str(c.id) for c in product.categories}
    if wanted != current:
        names = db.session.scalars(
            select(Category.name).where(Category.id.in_([int(i) for i in wanted if i.isdigit()]))
        ).all()
        lines.append(
            f"Categories: {', '.join(c.name for c in product.categories) or '—'} → "
            f"{', '.join(sorted(names)) or '—'}"
        )
    if len(lines) == 1:
        lines.append("No visible differences (saved as it is).")
    return Described(lines, _fp("product", product.id, product.updated_at))


def _product_visibility(args: dict[str, Any], form: MultiDict[str, str]) -> Described:
    product = _get(Product, args["product_id"])
    action = "Put back live" if form.get("live") == "1" else "Hide from the website"
    lines = [f"{action}: {product.name}"]
    if form.get("reason"):
        lines.append(f"Reason: {form.get('reason')}")
    return Described(lines, _fp("visibility", product.id, product.status))


def _image_upload(args: dict[str, Any], form: MultiDict[str, str]) -> Described:
    product = _get(Product, args["product_id"])
    upload = request.files.get("image")
    name = secure_filename(upload.filename) if upload and upload.filename else "no file"
    return Described(
        [f"Add a photo to {product.name}: “{form.get('alt', '')}” ({name})"],
        _fp("images", product.id, len(product.images)),
        notes=["The photo is checked and resized again when it is applied."],
    )


def _image_remove(args: dict[str, Any], form: MultiDict[str, str]) -> Described:
    image = _get(ProductImage, args["image_id"])
    return Described(
        [f"Remove photo {image.position + 1} (“{image.alt}”) from {image.product.name}"],
        _fp("image", image.id),
    )


def _sale_summary(sale: StudioSale) -> list[str]:
    lines = [
        f"Sale “{sale.name}”: {sale.discount_kind.value.replace('_', ' ')} {sale.amount}"
        f" — {sale.starts_at:%Y-%m-%d %H:%M} to {sale.ends_at:%Y-%m-%d %H:%M} UTC,"
        f" {len(sale.items)} item(s)"
    ]
    for item in sale.items[:12]:
        lines.append(
            f"  {_variant_label(item.variant)}: {_money(item.original_price_cents)} → "
            f"{_money(item.sale_price_cents)}"
        )
    if len(sale.items) > 12:
        lines.append(f"  … and {len(sale.items) - 12} more")
    return lines


def _sale_confirm(args: dict[str, Any], form: MultiDict[str, str]) -> Described:
    sale = _get(StudioSale, args["sale_id"])
    return Described(
        ["Confirm and schedule this sale:", *_sale_summary(sale)],
        _fp("sale", sale.id, sale.status),
    )


def _sale_cancel(args: dict[str, Any], form: MultiDict[str, str]) -> Described:
    sale = _get(StudioSale, args["sale_id"])
    return Described(
        [f"Cancel the sale “{sale.name}” (now {sale.status.value}); prices return to normal"],
        _fp("sale", sale.id, sale.status),
    )


def _blog_edit(args: dict[str, Any], form: MultiDict[str, str]) -> Described:
    post_id = args.get("post_id")
    if post_id is None:
        return Described([], needs_review=False)  # a new article starts as an unpublished draft
    post = _get(BlogPost, post_id)
    if post.status == BlogStatus.DRAFT:
        return Described([], needs_review=False)
    lines = [f"Edit the {post.status.value} article “{post.title}”"]
    for label, attr, key in (
        ("Title", "title", "title"),
        ("Summary", "excerpt", "excerpt"),
        ("Category", "category", "category"),
        ("Cover image", "cover_path", "cover_path"),
        ("SEO title", "seo_title", "seo_title"),
        ("SEO description", "seo_description", "seo_description"),
    ):
        before, after = getattr(post, attr) or "", (form.get(key) or "").strip()
        if before != after:
            lines.append(f"{label}: “{before or '—'}” → “{after or '—'}”")
    lines.append(f"Article text as submitted: “{_clip(form.get('body', ''), 600)}”")
    return Described(lines, _fp("blog", post.id, post.updated_at))


def _blog_publication(args: dict[str, Any], form: MultiDict[str, str]) -> Described:
    post = _get(BlogPost, args["post_id"])
    action = form.get("action", "")
    text = {
        "publish": f"Publish now: “{post.title}”",
        "schedule": f"Schedule “{post.title}” for {form.get('publish_at', '?')} ({TIMEZONE_NOTE})",
        "unpublish": f"Take down the article “{post.title}”",
    }.get(action, f"Change publication of “{post.title}” ({action})")
    return Described([text], _fp("blog-status", post.id, post.status, post.updated_at))


def _blog_revision(args: dict[str, Any], form: MultiDict[str, str]) -> Described:
    revision = _get(BlogRevision, args["revision_id"])
    post = revision.post
    if post.status == BlogStatus.DRAFT:
        return Described([], needs_review=False)
    return Described(
        [
            f"Restore the version of “{post.title}” saved {revision.created_at:%Y-%m-%d %H:%M}"
            " UTC (the live article changes)"
        ],
        _fp("blog", post.id, post.updated_at),
    )


def _post_label(post: ProductPost) -> str:
    return f"{post.kind.value} on {post.product.name}: “{_clip(post.title or post.body, 160)}”"


def _community_reply(args: dict[str, Any], form: MultiDict[str, str]) -> Described:
    post = _get(ProductPost, args["post_id"])
    return Described(
        [
            f"Public reply as GG Afrikan Eleganz to the {_post_label(post)}",
            f"Reply: “{_clip(form.get('body', ''), 1000)}”",
        ],
        _fp("post", post.id, post.status),
    )


def _community_moderate(args: dict[str, Any], form: MultiDict[str, str]) -> Described:
    post = _get(ProductPost, args["post_id"])
    action = {
        "hide": "Hide",
        "restore": "Show again",
        "restore_removed": "Restore (false positive)",
        "relevant": "Mark as relevant (show in full)",
        "not_relevant": "Mark as not relevant (collapse)",
    }.get(form.get("action", ""), form.get("action", "Change"))
    return Described([f"{action}: the {_post_label(post)}"], _fp("post", post.id, post.status))


def _word_add(args: dict[str, Any], form: MultiDict[str, str]) -> Described:
    kind = "on-topic word" if form.get("word_type") != "obscene" else "blocked word"
    return Described([f"Add {kind} “{form.get('word', '')}” ({form.get('language', '')})"])


def _word_remove(args: dict[str, Any], form: MultiDict[str, str]) -> Described:
    word = _get(ModerationWord, args["word_id"])
    return Described([f"Remove moderation word “{word.word}” ({word.language})"], _fp(word.id))


# Risky actions ask for the password again before they run or are held (owner plan: bulk price
# changes, removing a blog post; bulk stock added alongside). The password is never stored.
PASSWORD_FIELD = "step_up_password"  # noqa: S105 - a form field name, not a secret
STEP_UP: dict[str, Callable[[MultiDict[str, str]], bool]] = {
    "studio.price_bulk_apply": lambda form: True,
    "studio.inventory_bulk_apply": lambda form: True,
    "studio.product_bulk_apply": lambda form: True,
    "studio.blog_publication": lambda form: form.get("action") == "unpublish",
}

# Blog posting goes live without the owner for every account (owner, 2026-10-30: "groups + blog
# free, prices held"). Groups are never held. The password step-up above still applies.
LIVE_FOR_STAFF = frozenset(
    {
        "studio.blog_edit",
        "studio.blog_publication",
        "studio.blog_revision_restore",
        "studio.community_moderate",  # removing/restoring posts takes effect at once
    }
)

# endpoint -> (describer, the page's original roles for the permission pre-check)
HELD: dict[str, tuple[Describer, tuple[StudioRole, ...]]] = {
    "studio.inventory_update": (_inventory_update, (StudioRole.EDITOR, StudioRole.INVENTORY)),
    "studio.inventory_undo": (_inventory_undo, (StudioRole.EDITOR, StudioRole.INVENTORY)),
    "studio.inventory_bulk_apply": (_inventory_bulk, (StudioRole.EDITOR, StudioRole.INVENTORY)),
    "studio.price_update": (_price_update, (StudioRole.EDITOR,)),
    "studio.price_undo": (_price_undo, (StudioRole.EDITOR,)),
    "studio.price_bulk_apply": (_price_bulk, (StudioRole.EDITOR,)),
    "studio.product_edit": (_product_edit, (StudioRole.EDITOR,)),
    "studio.product_visibility": (_product_visibility, (StudioRole.EDITOR,)),
    "studio.product_bulk_apply": (_product_bulk, (StudioRole.EDITOR,)),
    "studio.product_undo": (_product_undo, (StudioRole.EDITOR,)),
    "studio.product_image_upload": (_image_upload, (StudioRole.EDITOR,)),
    "studio.product_image_remove": (_image_remove, (StudioRole.EDITOR,)),
    "studio.sale_confirm": (_sale_confirm, (StudioRole.EDITOR,)),
    "studio.sale_cancel": (_sale_cancel, (StudioRole.EDITOR,)),
    "studio.blog_edit": (_blog_edit, (StudioRole.EDITOR,)),
    "studio.blog_publication": (_blog_publication, (StudioRole.EDITOR,)),
    "studio.blog_revision_restore": (_blog_revision, (StudioRole.EDITOR,)),
    "studio.community_reply": (_community_reply, (StudioRole.SUPPORT,)),
    "studio.community_moderate": (_community_moderate, (StudioRole.SUPPORT,)),
    "studio.community_word_add": (_word_add, (StudioRole.SUPPORT,)),
    "studio.community_word_remove": (_word_remove, (StudioRole.SUPPORT,)),
}


# ---------- holding a change ----------


def pending_dir() -> Path:
    return Path(current_app.instance_path) / "studio_pending"


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _approver_email() -> str | None:
    cfg = current_app.config
    return cfg.get("STUDIO_APPROVER_EMAIL") or cfg.get("ORDER_NOTIFICATION_EMAIL")


def hold(user: StudioUser, endpoint: str, view_args: dict[str, Any], described: Described) -> None:
    from app.studio import service

    token = secrets.token_urlsafe(32)
    held_files = []
    for field_name, upload in request.files.items(multi=True):
        if not upload.filename:
            continue
        pending_dir().mkdir(parents=True, exist_ok=True)
        stored = f"{secrets.token_hex(16)}.upload"
        upload.save(pending_dir() / stored)
        held_files.append(
            {
                "field": field_name,
                "stored": stored,
                "filename": secure_filename(upload.filename) or "photo",
                "content_type": upload.mimetype or "application/octet-stream",
            }
        )
    upload_info = json.dumps(held_files) if held_files else None
    form_pairs = [
        (k, v) for k, v in request.form.items(multi=True) if k not in ("csrf_token", PASSWORD_FIELD)
    ]
    change = StudioChange(
        token_hash=_hash(token),
        user_id=user.id,
        endpoint=endpoint,
        view_args=json.dumps(view_args, default=str),
        form_data=json.dumps(form_pairs),
        upload=upload_info,
        summary="\n".join(described.lines + described.notes),
        fingerprint=described.fingerprint,
        status=ChangeStatus.PENDING,
        result="",
    )
    db.session.add(change)
    db.session.flush()
    service.audit(
        user,
        "studio.change.requested",
        "studio_change",
        str(change.id),
        {"endpoint": endpoint, "summary": described.lines},
    )
    db.session.commit()
    _notify_owner(change, token)


def _notify_owner(change: StudioChange, token: str) -> None:
    link = url_for("studio.review_change", token=token, _external=True)
    recipient = _approver_email()
    first = change.summary.splitlines()[0] if change.summary else change.endpoint
    body = (
        f"{change.user.username} made a change in the Staff Studio. It is NOT on the website"
        " until you approve it.\n\n"
        f"{change.summary}\n\n"
        f"Requested: {change.created_at:%Y-%m-%d %H:%M} UTC\n\n"
        f"Review and approve or reject (sign-in required):\n{link}\n"
    )
    try:
        sent = bool(recipient) and mail.send(str(recipient), f"Approve? {first[:120]}", body)
    except Exception:  # the change is saved; never fail the staff member's request over email
        log.exception("studio_change_notification_failed change=%s", change.id)
        sent = False
    if not sent:
        # Never log the link: it opens the approval page. The owner finds held changes on
        # their Studio "Changes" page instead.
        log.warning("studio_change_notification_not_sent change=%s", change.id)


def gate(endpoint: str, view: Callable[..., Any]) -> Callable[..., Any]:
    describer, legacy_roles = HELD[endpoint]

    def held_view(*args: Any, **kwargs: Any) -> Any:
        from app.studio import service

        if request.method != "POST" or g.get("studio_replay"):
            return view(*args, **kwargs)
        user = service.current_user()
        back = request.referrer or ""
        if not back.startswith(request.host_url):
            back = url_for("studio.dashboard")
        risky = endpoint in STEP_UP and STEP_UP[endpoint](request.form)
        if user is not None and not user.must_change_password and risky:
            if not permissions.can(user, endpoint, legacy_roles):
                abort(403)
            try:
                service.confirm_password(user, request.form.get(PASSWORD_FIELD, ""))
            except service.StudioError as exc:
                flash(str(exc), "error")
                return redirect(back, code=303)
        if (
            user is None
            or user.must_change_password
            or not user.requires_review
            or endpoint in LIVE_FOR_STAFF
        ):
            return view(*args, **kwargs)  # the view's own sign-in/role checks apply
        if not permissions.can(user, endpoint, legacy_roles):
            abort(403)
        described = describer(kwargs, request.form)
        if not described.needs_review:
            return view(*args, **kwargs)
        hold(user, endpoint, kwargs, described)
        flash(
            "Sent to the owner for approval. It will appear on the website once approved —"
            " see “My changes”.",
            "success",
        )
        if back == url_for("studio.dashboard"):
            back = url_for("studio.changes")
        return redirect(back, code=303)

    held_view.__name__ = getattr(view, "__name__", endpoint)
    held_view.__doc__ = view.__doc__
    return held_view


def install(app: Flask) -> None:
    for endpoint in HELD:
        if endpoint in app.view_functions:
            app.view_functions[endpoint] = gate(endpoint, app.view_functions[endpoint])


# ---------- deciding ----------


def fresh_review_token(change: StudioChange) -> str:
    """A new one-use review link for a waiting change (replaces the emailed one)."""
    token = secrets.token_urlsafe(32)
    change.token_hash = _hash(token)
    db.session.commit()
    return token


def change_by_token(token: str) -> StudioChange | None:
    if not token or len(token) > 100:
        return None
    return db.session.scalar(select(StudioChange).where(StudioChange.token_hash == _hash(token)))


def pending_count(user: StudioUser | None = None) -> int:
    query = select(func.count(StudioChange.id)).where(StudioChange.status == ChangeStatus.PENDING)
    if user is not None:
        query = query.where(StudioChange.user_id == user.id)
    return int(db.session.scalar(query) or 0)


def applied_this_week(user: StudioUser) -> int:
    """Changes by this person the owner approved in the last 7 days (the Studio star chart)."""
    since = utcnow() - timedelta(days=7)
    return int(
        db.session.scalar(
            select(func.count(StudioChange.id)).where(
                StudioChange.user_id == user.id,
                StudioChange.status == ChangeStatus.APPLIED,
                StudioChange.decided_at >= since,
            )
        )
        or 0
    )


def _held_files(change: StudioChange) -> list[dict[str, str]]:
    if not change.upload:
        return []
    data = json.loads(change.upload)
    return data if isinstance(data, list) else [data]  # older rows held one file


def _discard_upload(change: StudioChange) -> None:
    for info in _held_files(change):
        stored = info.get("stored", "")
        if stored and "/" not in stored and "\\" not in stored:
            (pending_dir() / stored).unlink(missing_ok=True)


def decide(change: StudioChange, approve: bool, decided_by: str) -> StudioChange:
    from app.studio import service

    # Claim the change in one conditional UPDATE so two simultaneous approvals (double click, two
    # tabs) can never replay it twice; only the request that claims it goes on.
    claimed = db.session.execute(
        update(StudioChange)
        .where(
            StudioChange.id == change.id,
            StudioChange.status == ChangeStatus.PENDING,
            StudioChange.decided_by.is_(None),
        )
        .values(decided_by=decided_by[:254])
        .execution_options(synchronize_session=False)
    ).rowcount  # type: ignore[attr-defined]
    db.session.commit()
    db.session.refresh(change)
    if claimed != 1:
        raise service.StudioError("This change was already decided.")
    try:
        if approve:
            status, message = _replay(change)
        else:
            status, message = ChangeStatus.REJECTED, "Rejected by the owner."
    except Exception:
        db.session.rollback()
        change.decided_by = None  # release the claim so the owner can try again
        db.session.commit()
        raise
    change.status = status
    change.result = message[:2000]
    change.decided_by = decided_by[:254]
    change.decided_at = utcnow()
    _discard_upload(change)
    service.audit(
        change.user,
        f"studio.change.{status.value}",
        "studio_change",
        str(change.id),
        {"decided_by": decided_by, "result": change.result},
    )
    db.session.commit()
    return change


def _replay(change: StudioChange) -> tuple[ChangeStatus, str]:
    """Run the held action as its author in a separate, clean request context."""
    app = current_app._get_current_object()  # type: ignore[attr-defined]
    view_args = json.loads(change.view_args)
    data: MultiDict[str, Any] = MultiDict(json.loads(change.form_data))
    for info in _held_files(change):
        held = pending_dir() / info["stored"]
        if not held.is_file():
            return ChangeStatus.FAILED, "The uploaded photo is no longer available."
        data.add(
            info["field"], (io.BytesIO(held.read_bytes()), info["filename"], info["content_type"])
        )
    path = url_for(change.endpoint, **view_args)
    describer, _roles = HELD[change.endpoint]
    with (
        app.app_context(),
        app.test_request_context(path, method="POST", data=data, base_url=request.host_url),
    ):
        author = db.session.get(StudioUser, change.user_id)
        if author is None or not author.active:
            return ChangeStatus.FAILED, "The staff account is disabled; nothing was applied."
        g.studio_user = author
        g.studio_replay = True
        try:
            now = describer(view_args, request.form)
        except HTTPException:
            return ChangeStatus.FAILED, "The item no longer exists; nothing was applied."
        if change.fingerprint and now.fingerprint != change.fingerprint:
            return (
                ChangeStatus.CONFLICT,
                "The item was changed by someone else after this request, so it was not"
                " applied (nothing was overwritten). Ask for a fresh change if still needed.",
            )
        try:
            response = app.make_response(app.view_functions[change.endpoint](**view_args))
        except HTTPException as exc:
            db.session.rollback()
            return ChangeStatus.FAILED, f"The Studio refused it ({exc.code})."
        flashes = cast(list[tuple[str, str]], get_flashed_messages(with_categories=True))
        errors = [message for category, message in flashes if category == "error"]
        if errors or not 300 <= response.status_code < 400:
            db.session.rollback()
            return ChangeStatus.FAILED, errors[0] if errors else "The Studio refused it."
        return ChangeStatus.APPLIED, (flashes[0][1] if flashes else "Applied.")
