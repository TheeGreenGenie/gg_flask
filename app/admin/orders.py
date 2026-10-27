"""Validated order and coupon mutations for the private administration UI."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select

from app.admin import service
from app.admin.models import AdminUser
from app.extensions import db
from app.orders import payments
from app.orders.models import (
    Coupon,
    DiscountType,
    Order,
    OrderNote,
    OrderStatus,
    PaymentStatus,
)


class OrderAdminError(ValueError):
    pass


ALLOWED_TRANSITIONS = {
    OrderStatus.PAID: {OrderStatus.PROCESSING},
    OrderStatus.PROCESSING: {OrderStatus.SHIPPED},
    OrderStatus.SHIPPED: {OrderStatus.COMPLETED},
}


def change_status(admin: AdminUser, order: Order, raw_status: str) -> None:
    try:
        status = OrderStatus(raw_status)
    except ValueError:
        raise OrderAdminError("Choose a valid order status.") from None
    if status == order.status:
        return
    if order.status == OrderStatus.PENDING_PAYMENT and status == OrderStatus.CANCELLED:
        cancel_unpaid(admin, order)
        return
    if status not in ALLOWED_TRANSITIONS.get(order.status, set()):
        raise OrderAdminError(f"An order in {order.status.value} cannot move to {status.value}.")
    before = order.status
    order.status = status
    service.audit(
        admin,
        "order.status.changed",
        "order",
        str(order.id),
        {"before": before.value, "after": status.value},
    )
    db.session.commit()


def cancel_unpaid(admin: AdminUser, order: Order) -> None:
    if order.status != OrderStatus.PENDING_PAYMENT:
        raise OrderAdminError("Paid orders must be refunded through their payment provider.")
    for hold in order.holds:
        if hold.released_at is None and hold.consumed_at is None:
            from app.catalog.models import utcnow

            hold.released_at = utcnow()
    if order.coupon_code:
        coupon = db.session.scalar(
            select(Coupon).where(Coupon.code == order.coupon_code).with_for_update()
        )
        if coupon:
            coupon.uses = max(coupon.uses - 1, 0)
    order.status = OrderStatus.CANCELLED
    order.payment_status = PaymentStatus.FAILED
    service.audit(admin, "order.cancelled", "order", str(order.id))
    db.session.commit()


def refund(admin: AdminUser, order: Order) -> None:
    try:
        payments.refund(order)
    except payments.PaymentError as exc:
        raise OrderAdminError(str(exc)) from None
    service.audit(
        admin,
        "order.refunded",
        "order",
        str(order.id),
        {"amount_cents": order.refunded_cents},
    )
    db.session.commit()


def add_note(admin: AdminUser, order: Order, raw_body: str) -> None:
    body = raw_body.strip()
    if not 1 <= len(body) <= 2000:
        raise OrderAdminError("A note must be between 1 and 2,000 characters.")
    db.session.add(OrderNote(order=order, admin_id=admin.id, body=body))
    service.audit(admin, "order.note.added", "order", str(order.id))
    db.session.commit()


def resend_email(admin: AdminUser, order: Order) -> None:
    if order.payment_status != PaymentStatus.PAID:
        raise OrderAdminError("Confirmation email is available only for paid orders.")
    payments.send_order_emails(order)
    service.audit(admin, "order.email.resent", "order", str(order.id))
    db.session.commit()


def save_coupon(admin: AdminUser, coupon: Coupon | None, form: dict[str, str]) -> Coupon:
    code = form.get("code", "").strip().upper()
    if not 2 <= len(code) <= 40 or not code.replace("-", "").isalnum():
        raise OrderAdminError("Coupon codes may contain letters, numbers, and hyphens.")
    duplicate = db.session.scalar(select(Coupon).where(Coupon.code == code))
    if duplicate and duplicate.id != (coupon.id if coupon else None):
        raise OrderAdminError("That coupon code already exists.")
    try:
        kind = DiscountType(form.get("discount_type", ""))
        amount = int(form.get("amount", ""))
        minimum = int(form.get("minimum_subtotal_cents", "0"))
        max_uses = int(form["max_uses"]) if form.get("max_uses", "").strip() else None
    except ValueError:
        raise OrderAdminError("Amounts and limits must be whole numbers.") from None
    if amount <= 0 or (kind == DiscountType.PERCENT and amount > 100):
        raise OrderAdminError("Enter a valid discount amount.")
    if minimum < 0 or (max_uses is not None and max_uses < 1):
        raise OrderAdminError("Minimum spend and use limit cannot be negative.")
    starts = _date(form.get("starts_at", ""), end=False)
    ends = _date(form.get("ends_at", ""), end=True)
    if starts and ends and ends <= starts:
        raise OrderAdminError("The end date must follow the start date.")
    coupon = coupon or Coupon(code=code, discount_type=kind, amount=amount)
    coupon.code = code
    coupon.discount_type = kind
    coupon.amount = amount
    coupon.minimum_subtotal_cents = minimum
    coupon.max_uses = max_uses
    coupon.starts_at = starts
    coupon.ends_at = ends
    coupon.active = form.get("active") == "1"
    db.session.add(coupon)
    db.session.flush()
    service.audit(admin, "coupon.saved", "coupon", str(coupon.id), {"code": code})
    db.session.commit()
    return coupon


def _date(raw: str, *, end: bool) -> datetime | None:
    if not raw.strip():
        return None
    try:
        value = datetime.strptime(raw, "%Y-%m-%d").replace(tzinfo=UTC)
    except ValueError:
        raise OrderAdminError("Use a valid date.") from None
    if end:
        value = value.replace(hour=23, minute=59, second=59)
    return value
