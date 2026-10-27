"""Editorial, customer-service, policy, and legacy notice pages."""

from __future__ import annotations

from email.utils import parseaddr

from flask import Blueprint, flash, redirect, render_template, request, url_for
from werkzeug.wrappers import Response

from app import mail, ratelimit, site_content
from app.content.data import FAQS, POLICIES
from app.store.content import POSTS

bp = Blueprint("content", __name__)

# Subject list from the previous site's contact form.
CONTACT_TOPICS = {
    "general": "General Inquiry",
    "product": "Product Question",
    "order": "Order Status",
    "shipping": "Shipping",
    "returns": "Returns & Exchanges",
    "wholesale": "Wholesale Inquiry",
    "other": "Other",
}


@bp.get("/about-us/")
def about() -> str:
    return render_template("content/about.html")


@bp.route("/contact-us/", methods=["GET", "POST"])
def contact() -> str | Response:
    form = {
        key: request.form.get(key, "").strip()
        for key in ("name", "email", "phone", "topic", "message")
    }
    error = ""
    if request.method == "POST":
        if not ratelimit.allow("contact", ratelimit.client_key(), 5, 3600):
            error = "Too many messages were sent. Please wait and try again later."
        elif not 1 <= len(form["name"]) <= 100:
            error = "Enter your name."
        elif not _valid_email(form["email"]):
            error = "Enter a valid email address."
        elif len(form["phone"]) > 30:
            error = "Enter a shorter phone number."
        elif form["topic"] not in CONTACT_TOPICS:
            error = "Choose a subject."
        elif not 10 <= len(form["message"]) <= 4000:
            error = "Enter a message between 10 and 4,000 characters."
        else:
            topic = CONTACT_TOPICS[form["topic"]]
            body = (
                f"Website contact form\n\nName: {form['name']}\nEmail: {form['email']}\n"
                f"Phone: {form['phone'] or 'Not supplied'}\nSubject: {topic}\n\n"
                f"Message:\n{form['message']}"
            )
            if mail.send(site_content.CONTACT_EMAIL, f"Website enquiry: {topic}", body):
                flash("Thanks—your message has been sent. We'll get back to you soon.", "success")
                return redirect(url_for("content.contact"))
            error = (
                "The contact form is temporarily unavailable. Please email or call us using "
                "the details on this page."
            )
    return render_template(
        "content/contact.html", form=form, error=error, topics=CONTACT_TOPICS, hide_newsletter=True
    )


@bp.get("/faq/")
def faq() -> str:
    return render_template("content/faq.html", faqs=FAQS)


@bp.get("/privacy-policy/")
def privacy() -> str:
    return _policy("privacy")


@bp.get("/terms-of-service/")
def terms() -> str:
    return _policy("terms")


@bp.get("/shipping-information/")
def shipping() -> str:
    return _policy("shipping")


@bp.get("/returns-exchanges/")
def returns() -> str:
    return _policy("returns")


@bp.get("/blog/<slug>/")
def post(slug: str) -> str | tuple[str, int]:
    ordered = list(reversed(POSTS))  # POSTS is newest first; navigate oldest -> newest
    index = next((i for i, entry in enumerate(ordered) if entry["slug"] == slug), None)
    if index is None:
        return render_template("content/not_found.html"), 404
    return render_template(
        "content/post.html",
        post=ordered[index],
        previous_post=ordered[index - 1] if index > 0 else None,
        next_post=ordered[index + 1] if index + 1 < len(ordered) else None,
    )


@bp.get("/support/")
def support_redirect() -> Response:
    return redirect(url_for("content.contact"), code=301)


@bp.get("/my-account/")
@bp.get("/my-account/<path:legacy_path>/")
@bp.get("/order-tracking/")
def relaunch_notice(legacy_path: str = "") -> str:
    return render_template("content/relaunch.html")


@bp.get("/portfolio/")
def retired_portfolio() -> tuple[str, int]:
    return render_template("content/retired.html"), 410


def _policy(key: str) -> str:
    return render_template("content/policy.html", page=POLICIES[key])


def _valid_email(value: str) -> bool:
    _, address = parseaddr(value)
    return address == value and 3 <= len(value) <= 254 and "@" in value.rsplit(".", 1)[0]
