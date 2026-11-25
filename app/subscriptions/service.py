"""Subscription business logic.

Independent of Flask and auth: every function takes the caller's customer_id (the routes
pass g.customer_id, never a value from the request) and a SQLAlchemy Session. Functions
flush but never commit; the caller owns the transaction. Another customer's subscription
raises NotFound, so its existence is never revealed.

Mutations lock the subscription row (SELECT ... FOR UPDATE) so they can't interleave with
the scheduler or a WhatsApp reply for the same subscription.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app import constants as C
from app.errors import Conflict, NotFound, ValidationError
from app.models import Customer, DeliveryOccurrence, Inventory, Product, Subscription
from database.models import Order, OrderItem
from app.notifications.sender import can_message
from app.subscriptions import recommendations
from app.subscriptions.recommendations import EngineRecommendation
from app.timeutil import LOCAL_TZ, local_date, now_utc, today_local
from database.models import WEEKDAYS

# Occurrences that haven't reached a final state yet.
PENDING_OCCURRENCE_STATUSES = ("SCHEDULED", "NOTIFIED", "APPROVED", "DELAYED")
STATUS_ORDER = {"ACTIVE": 0, "PAUSED": 1, "CANCELLED": 2}
CENT = Decimal("0.01")


# --------------------------------------------------------------------------- #
# Recommendations (Sameer's engine, see app/subscriptions/recommendations.py)
# --------------------------------------------------------------------------- #
def get_recommendation(session: Session, customer_id: str, product_id: str,
                       rec_id: str | None = None) -> EngineRecommendation | None:
    """The engine's recommended quantity/frequency for this customer + product, or None.
    `rec_id` is accepted for compatibility but unused: the engine identifies a
    recommendation by customer and product."""
    return recommendations.recommend(session, customer_id, product_id)


# --------------------------------------------------------------------------- #
# Input validation
# --------------------------------------------------------------------------- #
def _reject_unknown(data: dict, allowed: set[str]) -> None:
    if not isinstance(data, dict):
        raise ValidationError("Request body must be a JSON object")
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ValidationError(f"Unknown field(s): {', '.join(unknown)}", details={"fields": unknown})


def _int_in(name: str, value: Any, lo: int, hi: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(f"{name} must be a whole number", details={"field": name})
    if not lo <= value <= hi:
        raise ValidationError(f"{name} must be between {lo} and {hi}", details={"field": name})
    return value


def _date(name: str, value: Any) -> dt.date:
    if isinstance(value, dt.date) and not isinstance(value, dt.datetime):
        return value
    if isinstance(value, str) and len(value) == 10:
        try:
            return dt.date.fromisoformat(value)
        except ValueError:
            pass
    raise ValidationError(f"{name} must be a date in YYYY-MM-DD format", details={"field": name})


def _slot(value: Any) -> str | None:
    if value is None:
        return None
    if value not in C.DELIVERY_SLOTS:
        raise ValidationError(f"preferred_slot must be one of {', '.join(C.DELIVERY_SLOTS)}",
                              details={"field": "preferred_slot"})
    return value


def _clamp(value: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, value))


# --------------------------------------------------------------------------- #
# Dates
# --------------------------------------------------------------------------- #
def earliest_delivery_date(today: dt.date) -> dt.date:
    return today + dt.timedelta(days=C.MIN_LEAD_DAYS)


def default_first_delivery_date(customer: Customer, today: dt.date) -> dt.date:
    """First date on or after the earliest allowed date that falls on the customer's preferred weekday."""
    earliest = earliest_delivery_date(today)
    target = WEEKDAYS.index(customer.preferred_weekday)
    return earliest + dt.timedelta(days=(target - earliest.weekday()) % 7)


def _check_delivery_date(name: str, d: dt.date, today: dt.date) -> None:
    earliest = earliest_delivery_date(today)
    latest = today + dt.timedelta(days=C.MAX_SCHEDULE_AHEAD_DAYS)
    if not earliest <= d <= latest:
        raise ValidationError(f"{name} must be between {earliest.isoformat()} and {latest.isoformat()}",
                              details={"field": name})


def _occurrence_dates(session: Session, sub: Subscription) -> set[dt.date]:
    return set(session.scalars(select(DeliveryOccurrence.scheduled_date)
                               .where(DeliveryOccurrence.subscription_id == sub.subscription_id)))


def next_free_date(session: Session, sub: Subscription, start: dt.date, today: dt.date) -> dt.date:
    """Roll `start` forward by the subscription's frequency (keeping its cadence) until it is
    schedulable and no occurrence exists for it yet (one occurrence per subscription + date)."""
    taken = _occurrence_dates(session, sub)
    earliest = earliest_delivery_date(today)
    d = start
    while d < earliest or d in taken:
        d += dt.timedelta(days=sub.frequency_days)
    return d


# --------------------------------------------------------------------------- #
# Lookups
# --------------------------------------------------------------------------- #
def _get_product(session: Session, product_id: Any) -> Product:
    if not isinstance(product_id, str) or not product_id:
        raise ValidationError("product_id is required", details={"field": "product_id"})
    product = session.get(Product, product_id)
    if product is None:
        raise NotFound("Product not found")
    return product


def _get_customer(session: Session, customer_id: str) -> Customer:
    customer = session.get(Customer, customer_id)
    if customer is None:
        raise NotFound("Customer not found")
    return customer


def _get_owned(session: Session, customer_id: str, subscription_id: int, *, lock: bool = False) -> Subscription:
    q = select(Subscription).where(Subscription.subscription_id == subscription_id,
                                   Subscription.customer_id == customer_id)
    if lock:
        q = q.with_for_update().execution_options(populate_existing=True)
    sub = session.scalars(q).one_or_none()
    if sub is None:
        raise NotFound("Subscription not found")
    return sub


def _require_messaging(session: Session, customer: Customer) -> None:
    if not can_message(session, customer):
        raise Conflict("Connect chat notifications to subscribe: delivery reminders and approvals happen there.",
                       code="messaging_required")


def _live_subscription(session: Session, customer_id: str, product_id: str) -> Subscription | None:
    return session.scalars(select(Subscription).where(
        Subscription.customer_id == customer_id, Subscription.product_id == product_id,
        Subscription.status != "CANCELLED")).one_or_none()


def _pending_occurrences(session: Session, sub: Subscription) -> list[DeliveryOccurrence]:
    return list(session.scalars(
        select(DeliveryOccurrence)
        .where(DeliveryOccurrence.subscription_id == sub.subscription_id,
               DeliveryOccurrence.status.in_(PENDING_OCCURRENCE_STATUSES))
        .with_for_update()))


def _cancel_pending(session: Session, sub: Subscription) -> None:
    for occ in _pending_occurrences(session, sub):
        occ.status = "CANCELLED"


# --------------------------------------------------------------------------- #
# Serialisation
# --------------------------------------------------------------------------- #
def _money(d: Decimal) -> str:
    return str(d.quantize(CENT))


def _iso(v: dt.date | dt.datetime | None) -> str | None:
    return v.isoformat() if v is not None else None


def _product_summary(p: Product) -> dict[str, Any]:
    return {"product_id": p.product_id, "product_name": p.product_name, "brand": p.brand,
            "pack_size": p.pack_size, "image_url": p.image_url, "category": p.category}


def subscription_to_dict(sub: Subscription) -> dict[str, Any]:
    p = sub.product
    unit = sub.unit_price_at_subscription
    current = p.discount_price
    return {
        "subscription_id": sub.subscription_id,
        "status": sub.status,
        "product": _product_summary(p),
        "quantity": sub.quantity,
        "frequency_days": sub.frequency_days,
        "next_delivery_date": _iso(sub.next_delivery_date),
        "preferred_slot": sub.preferred_slot,
        "paused_until": _iso(sub.paused_until),
        "regular_price": _money(p.price),
        "unit_price": _money(unit),                     # subscriber price locked in at subscription time
        "current_subscriber_price": _money(current),
        "price_changed": current != unit,
        "total_per_delivery": _money(unit * sub.quantity),
        "saving_per_delivery": _money(max(p.price - unit, Decimal(0)) * sub.quantity),
        "recommended_quantity": sub.recommended_quantity,
        "recommended_frequency_days": sub.recommended_frequency_days,
        "created_at": _iso(sub.created_at),
        "updated_at": _iso(sub.updated_at),
        "cancelled_at": _iso(sub.cancelled_at),
    }


# --------------------------------------------------------------------------- #
# Read operations
# --------------------------------------------------------------------------- #
def product_offer(session: Session, product_id: str, customer_id: str | None = None) -> dict[str, Any]:
    """PDP data: regular price struck through, subscriber price, and the saving."""
    p = _get_product(session, product_id)
    inv = session.get(Inventory, p.product_id)
    saving = p.price - p.discount_price
    out = {
        **_product_summary(p),
        "subcategory": p.subcategory,
        "regular_price": _money(p.price),
        "subscriber_price": _money(p.discount_price),
        "saving_per_unit": _money(saving),
        "saving_percent": float((saving * 100 / p.price).quantize(Decimal("0.1"))),
        # Some staples (milk, bread, ...) are sold at MRP: no subscriber discount, so the PDP
        # hides the struck-through price and savings banner.
        "has_discount": saving > 0,
        "subscribable": p.replenishable,
        "in_stock": bool(inv and inv.available_qty > 0),
    }
    if customer_id is not None:
        live = _live_subscription(session, customer_id, p.product_id)
        out["subscription_id"] = live.subscription_id if live else None
        out["messaging_ready"] = can_message(session, _get_customer(session, customer_id))
    return out


def subscription_draft(session: Session, customer_id: str, product_id: str, rec_id: str | None = None,
                       today: dt.date | None = None) -> dict[str, Any]:
    """Pre-filled values for the 'new subscription' card on the dashboard."""
    today = today or today_local()
    p = _get_product(session, product_id)
    if not p.replenishable:
        raise ValidationError("This product isn't available on subscription", code="not_subscribable")
    customer = _get_customer(session, customer_id)
    rec = get_recommendation(session, customer_id, p.product_id, rec_id)
    quantity = _clamp(rec.quantity, C.QUANTITY_MIN, C.QUANTITY_MAX) if rec else C.DEFAULT_QUANTITY
    frequency = (_clamp(rec.frequency_days, C.FREQUENCY_DAYS_MIN, C.FREQUENCY_DAYS_MAX) if rec
                 else C.DEFAULT_FREQUENCY_DAYS)
    live = _live_subscription(session, customer_id, p.product_id)
    return {
        "product": product_offer(session, p.product_id),
        "quantity": quantity,
        "frequency_days": frequency,
        "first_delivery_date": default_first_delivery_date(customer, today).isoformat(),
        "preferred_slot": None,
        "unit_price": _money(p.discount_price),
        "total_per_delivery": _money(p.discount_price * quantity),
        "saving_per_delivery": _money((p.price - p.discount_price) * quantity),
        "recommendation": {
            "source": "recommendation" if rec else "default",
            "quantity": rec.quantity if rec else None,
            "frequency_days": rec.frequency_days if rec else None,
            "rank": rec.rank if rec else None,
            "score": rec.score if rec else None,
            "status": rec.status if rec else None,
            "reason": rec.reason if rec else None,
        },
        "existing_subscription": subscription_to_dict(live) if live else None,
        # Subscribing needs chat notifications (reminders and approvals happen there); the UI
        # asks the customer to connect them first when this is false.
        "messaging_ready": can_message(session, customer),
        "limits": {
            "quantity": [C.QUANTITY_MIN, C.QUANTITY_MAX],
            "frequency_days": [C.FREQUENCY_DAYS_MIN, C.FREQUENCY_DAYS_MAX],
            "earliest_delivery_date": earliest_delivery_date(today).isoformat(),
            "delivery_slots": list(C.DELIVERY_SLOTS),
        },
    }


def list_subscriptions(session: Session, customer_id: str, include_cancelled: bool = False) -> list[dict[str, Any]]:
    """Active first, then paused, then cancelled; soonest delivery first within each group."""
    q = (select(Subscription).where(Subscription.customer_id == customer_id)
         .options(selectinload(Subscription.product)))
    if not include_cancelled:
        q = q.where(Subscription.status != "CANCELLED")
    subs = session.scalars(q).all()
    subs = sorted(subs, key=lambda s: (STATUS_ORDER[s.status], s.next_delivery_date or dt.date.max,
                                       -s.subscription_id))
    return [subscription_to_dict(s) for s in subs]


def get_subscription(session: Session, customer_id: str, subscription_id: int) -> dict[str, Any]:
    return subscription_to_dict(_get_owned(session, customer_id, subscription_id))


# --------------------------------------------------------------------------- #
# Mutations
# --------------------------------------------------------------------------- #
CREATE_FIELDS = {"product_id", "quantity", "frequency_days", "first_delivery_date", "preferred_slot", "rec_id"}
UPDATE_FIELDS = {"quantity", "frequency_days", "preferred_slot", "next_delivery_date", "status", "paused_until"}


def create_subscription(session: Session, customer_id: str, data: dict,
                        today: dt.date | None = None) -> dict[str, Any]:
    _reject_unknown(data, CREATE_FIELDS)
    today = today or today_local()
    p = _get_product(session, data.get("product_id"))
    if not p.replenishable:
        raise ValidationError("This product isn't available on subscription", code="not_subscribable")
    customer = _get_customer(session, customer_id)
    _require_messaging(session, customer)
    rec = get_recommendation(session, customer_id, p.product_id, data.get("rec_id"))

    quantity = _int_in("quantity", data.get("quantity", rec.quantity if rec else C.DEFAULT_QUANTITY),
                       C.QUANTITY_MIN, C.QUANTITY_MAX)
    frequency = _int_in("frequency_days",
                        data.get("frequency_days", rec.frequency_days if rec else C.DEFAULT_FREQUENCY_DAYS),
                        C.FREQUENCY_DAYS_MIN, C.FREQUENCY_DAYS_MAX)
    if data.get("first_delivery_date") is not None:
        first = _date("first_delivery_date", data["first_delivery_date"])
        _check_delivery_date("first_delivery_date", first, today)
    else:
        first = default_first_delivery_date(customer, today)
    slot = _slot(data.get("preferred_slot"))

    def already_subscribed(existing_id: int | None) -> Conflict:
        return Conflict("You already have a subscription for this product", code="already_subscribed",
                        details={"subscription_id": existing_id})

    live = _live_subscription(session, customer_id, p.product_id)
    if live is not None:
        raise already_subscribed(live.subscription_id)

    sub = Subscription(
        customer_id=customer_id, product_id=p.product_id, quantity=quantity, frequency_days=frequency,
        unit_price_at_subscription=p.discount_price, status="ACTIVE", next_delivery_date=first,
        preferred_slot=slot,
        recommended_quantity=rec.quantity if rec else None,
        recommended_frequency_days=rec.frequency_days if rec else None,
    )
    session.add(sub)
    try:
        with session.begin_nested():  # savepoint: a lost race on uq_live_subscription stays recoverable
            session.flush()
    except IntegrityError as e:
        if "uq_live_subscription" in str(e.orig):
            live = _live_subscription(session, customer_id, p.product_id)
            raise already_subscribed(live.subscription_id if live else None) from None
        raise
    session.refresh(sub)
    return subscription_to_dict(sub)


def update_subscription(session: Session, customer_id: str, subscription_id: int, data: dict,
                        today: dt.date | None = None) -> dict[str, Any]:
    """Edit quantity / frequency / slot / next date, pause (status=PAUSED, optional paused_until)
    or resume (status=ACTIVE). Cancelling is a separate operation."""
    _reject_unknown(data, UPDATE_FIELDS)
    if not data:
        raise ValidationError("Nothing to update")
    today = today or today_local()
    sub = _get_owned(session, customer_id, subscription_id, lock=True)
    if sub.status == "CANCELLED":
        raise Conflict("This subscription is cancelled", code="cancelled")

    status = data.get("status")
    if status is not None and status not in ("ACTIVE", "PAUSED"):
        raise ValidationError("status must be ACTIVE or PAUSED (cancel with DELETE)", details={"field": "status"})

    if "quantity" in data:
        sub.quantity = _int_in("quantity", data["quantity"], C.QUANTITY_MIN, C.QUANTITY_MAX)
    if "frequency_days" in data:
        sub.frequency_days = _int_in("frequency_days", data["frequency_days"],
                                     C.FREQUENCY_DAYS_MIN, C.FREQUENCY_DAYS_MAX)
    if "preferred_slot" in data:
        sub.preferred_slot = _slot(data["preferred_slot"])
    if "next_delivery_date" in data:
        d = _date("next_delivery_date", data["next_delivery_date"])
        _check_delivery_date("next_delivery_date", d, today)
        if d != sub.next_delivery_date:
            if _pending_occurrences(session, sub):
                raise Conflict("A reminder for the upcoming delivery is already out. Reply to it on "
                               "WhatsApp or skip it first.", code="delivery_in_progress")
            if d in _occurrence_dates(session, sub):
                raise ValidationError("A delivery on that date was already handled; pick another date",
                                      details={"field": "next_delivery_date"})
            sub.next_delivery_date = d

    if status == "PAUSED":
        until = None
        if data.get("paused_until") is not None:
            until = _date("paused_until", data["paused_until"])
            if until <= today:
                raise ValidationError("paused_until must be in the future", details={"field": "paused_until"})
        if sub.status != "PAUSED":
            _cancel_pending(session, sub)
        sub.status = "PAUSED"
        sub.paused_until = until
    elif status == "ACTIVE":
        if "paused_until" in data:
            raise ValidationError("paused_until can't be set when resuming", details={"field": "paused_until"})
        if sub.status == "PAUSED":
            _require_messaging(session, sub.customer)
            resume(session, sub, today)
    elif "paused_until" in data:
        raise ValidationError("paused_until can only be set together with status=PAUSED",
                              details={"field": "paused_until"})

    session.flush()
    session.refresh(sub)
    return subscription_to_dict(sub)


def resume(session: Session, sub: Subscription, today: dt.date) -> None:
    """Reactivate a paused subscription, keeping its cadence. Also used by the scheduler
    when paused_until is reached."""
    sub.status = "ACTIVE"
    sub.paused_until = None
    start = sub.next_delivery_date or default_first_delivery_date(sub.customer, today)
    sub.next_delivery_date = next_free_date(session, sub, start, today)


def cancel_subscription(session: Session, customer_id: str, subscription_id: int) -> dict[str, Any]:
    """Cancel (idempotent). Pending deliveries are cancelled too; past orders are untouched."""
    sub = _get_owned(session, customer_id, subscription_id, lock=True)
    if sub.status != "CANCELLED":
        _cancel_pending(session, sub)
        sub.status = "CANCELLED"
        sub.cancelled_at = now_utc()
        sub.next_delivery_date = None
        sub.paused_until = None
        session.flush()
        session.refresh(sub)
    return subscription_to_dict(sub)


def get_owned_delivery(session: Session, customer_id: str, occurrence_id: int, *,
                       lock: bool = False) -> tuple[Subscription, DeliveryOccurrence]:
    """A delivery and its subscription, if the subscription belongs to customer_id (else
    NotFound). With lock=True both rows are locked, subscription first (same order as the
    scheduler)."""
    row = session.execute(select(DeliveryOccurrence.subscription_id).join(Subscription).where(
        DeliveryOccurrence.occurrence_id == occurrence_id, Subscription.customer_id == customer_id)).first()
    if row is None:
        raise NotFound("Delivery not found")
    sub = _get_owned(session, customer_id, row.subscription_id, lock=lock)
    q = select(DeliveryOccurrence).where(DeliveryOccurrence.occurrence_id == occurrence_id)
    if lock:
        q = q.with_for_update().execution_options(populate_existing=True)
    return sub, session.scalars(q).one()


def advance_cycle(session: Session, sub: Subscription, occ: DeliveryOccurrence, today: dt.date) -> None:
    """This delivery's cycle is settled: move the subscription to its next cycle, keeping the
    original cadence (delays never shift it)."""
    if sub.status == "ACTIVE" and sub.next_delivery_date == occ.scheduled_date:
        sub.next_delivery_date = next_free_date(
            session, sub, occ.scheduled_date + dt.timedelta(days=sub.frequency_days), today)


def ensure_awaiting_reply(sub: Subscription, occ: DeliveryOccurrence, now: dt.datetime) -> None:
    if sub.status != "ACTIVE" or occ.status != "NOTIFIED":
        raise Conflict("This delivery is no longer waiting for your reply", code="delivery_finalised")
    if occ.response_deadline is not None and now > occ.response_deadline:
        raise Conflict("The time to reply to this delivery has passed", code="approval_expired")


def approve_delivery(session: Session, customer_id: str, occurrence_id: int, slot: Any,
                     now: dt.datetime | None = None) -> dict[str, Any]:
    """The customer approved a reminder and picked a slot. In one transaction: lock the stock
    row, check and decrement it, create the order (source='subscription'), and settle the
    cycle. Approving accepts the current subscriber price.

    Returns {"status": "order_placed", ...} or {"status": "out_of_stock", ...}; both are
    state changes the caller must commit.
    """
    now = now or now_utc()
    today = local_date(now)
    if slot is None:
        raise ValidationError("Pick a delivery slot", details={"field": "slot"})
    slot = _slot(slot)
    sub, occ = get_owned_delivery(session, customer_id, occurrence_id, lock=True)
    ensure_awaiting_reply(sub, occ, now)
    product, qty, when = sub.product, sub.quantity, delivery_date(occ)

    stock = session.scalars(select(Inventory).where(Inventory.product_id == sub.product_id)
                            .with_for_update()).one_or_none()
    if stock is None or stock.available_qty < qty:
        occ.status, occ.skip_reason, occ.responded_at = "OUT_OF_STOCK", "OUT_OF_STOCK", now
        advance_cycle(session, sub, occ, today)
        session.flush()
        return {"status": "out_of_stock", "product": product.product_name, "delivery_date": when.isoformat(),
                "next_delivery_date": _iso(sub.next_delivery_date)}

    price = product.discount_price
    total = price * qty
    stock.available_qty -= qty
    placed_at = now.astimezone(LOCAL_TZ).replace(tzinfo=None)    # orders.order_datetime is naive IST
    order_id = f"SUB-{occ.occurrence_id}"
    session.add(Order(order_id=order_id, customer_id=customer_id, order_date=placed_at.date(),
                      order_datetime=placed_at, item_count=1, unit_count=qty, order_total=total,
                      source="subscription", delivery_slot=slot))
    session.flush()
    session.add(OrderItem(order_item_id=f"SUBI-{occ.occurrence_id}", order_id=order_id,
                          product_id=product.product_id, product_type=product.product_type,
                          quantity=qty, unit_price=price, line_total=total))
    occ.status, occ.order_id, occ.delivery_slot = "ORDER_PLACED", order_id, slot
    occ.unit_price_at_order, occ.responded_at = price, now
    sub.unit_price_at_subscription = price             # the accepted price is the new reference
    advance_cycle(session, sub, occ, today)
    session.flush()
    return {"status": "order_placed", "order_id": order_id, "product": product.product_name,
            "quantity": qty, "delivery_date": when.isoformat(), "slot": slot, "total": _money(total),
            "next_delivery_date": _iso(sub.next_delivery_date)}


def skip_delivery(session: Session, customer_id: str, occurrence_id: int,
                  now: dt.datetime | None = None) -> dict[str, Any]:
    """The customer chose "Skip this delivery" on a reminder."""
    now = now or now_utc()
    sub, occ = get_owned_delivery(session, customer_id, occurrence_id, lock=True)
    ensure_awaiting_reply(sub, occ, now)
    occ.status, occ.skip_reason, occ.responded_at = "SKIPPED", "USER_SKIP", now
    advance_cycle(session, sub, occ, local_date(now))
    session.flush()
    return {"skipped_date": delivery_date(occ).isoformat(), "next_delivery_date": _iso(sub.next_delivery_date)}


def delivery_date(occ: DeliveryOccurrence) -> dt.date:
    """When this delivery actually happens: its cycle date plus any delay."""
    return occ.scheduled_date + dt.timedelta(days=occ.delay_days or 0)


def delay_options(sub: Subscription, occ: DeliveryOccurrence) -> list[int]:
    """Delay choices (days) still possible for this delivery: the total delay stays within
    MAX_TOTAL_DELAY_DAYS and strictly before the next cycle."""
    used = occ.delay_days or 0
    return [n for n in C.DELAY_OPTIONS_DAYS
            if used + n <= C.MAX_TOTAL_DELAY_DAYS and used + n < sub.frequency_days]


def delay_delivery(session: Session, customer_id: str, occurrence_id: int, days: int,
                   now: dt.datetime | None = None) -> DeliveryOccurrence:
    """Customer chose "Delay N days" on a reminder. The delivery moves by N days and goes back
    to DELAYED: the scheduler sends a fresh reminder before the new date, which must be
    approved again. The subscription's cycle is unchanged. Called by the chat flow."""
    now = now or now_utc()
    sub, occ = get_owned_delivery(session, customer_id, occurrence_id, lock=True)
    if sub.status != "ACTIVE" or occ.status != "NOTIFIED":
        raise Conflict("This delivery can no longer be delayed", code="delivery_finalised")
    if days not in delay_options(sub, occ):
        raise Conflict(f"This delivery can't be delayed by {days} day(s)", code="delay_not_possible",
                       details={"options": delay_options(sub, occ)})
    occ.delay_days = (occ.delay_days or 0) + days
    occ.status = "DELAYED"
    occ.responded_at = now
    occ.response_deadline = None
    session.flush()
    return occ


def skip_next_delivery(session: Session, customer_id: str, subscription_id: int,
                       today: dt.date | None = None) -> dict[str, Any]:
    """Skip the upcoming delivery and move to the next cycle (from the dashboard; the
    WhatsApp Skip button reaches the same state through the notification flow)."""
    today = today or today_local()
    sub = _get_owned(session, customer_id, subscription_id, lock=True)
    if sub.status != "ACTIVE":
        raise Conflict("Only active subscriptions can skip a delivery", code="not_active")
    d = sub.next_delivery_date or default_first_delivery_date(sub.customer, today)

    occ = session.scalars(select(DeliveryOccurrence)
                          .where(DeliveryOccurrence.subscription_id == sub.subscription_id,
                                 DeliveryOccurrence.scheduled_date == d)
                          .with_for_update()).one_or_none()
    now = now_utc()
    if occ is None:
        session.add(DeliveryOccurrence(subscription_id=sub.subscription_id, scheduled_date=d, status="SKIPPED",
                                       skip_reason="USER_SKIP", responded_at=now))
    elif occ.status in PENDING_OCCURRENCE_STATUSES:
        occ.status = "SKIPPED"
        occ.skip_reason = "USER_SKIP"
        occ.responded_at = now
    else:
        raise Conflict(f"The delivery on {d.isoformat()} can no longer be skipped ({occ.status.lower()})",
                       code="delivery_finalised")
    session.flush()
    sub.next_delivery_date = next_free_date(session, sub, d + dt.timedelta(days=sub.frequency_days), today)
    session.flush()
    session.refresh(sub)
    return {"skipped_date": d.isoformat(), "subscription": subscription_to_dict(sub)}
