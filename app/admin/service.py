"""Admin panel queries and actions (no Flask here; routes.py handles HTTP).

Stock changes are atomic SQL updates, never read-modify-write, and each one writes an
inventory_movements row in the same transaction. After a stock increase the caller commits
and then calls release_after_restock(), so held deliveries of that product get their reminder.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app import constants as C
from app.errors import Conflict, NotFound, ValidationError
from app.models import (
    CancellationRequest, Customer, DeliveryOccurrence, Inventory, InventoryMovement, Product, Subscription,
)
from app.notifications.sender import (
    TEMPLATE_OPS_DELAYED, TEMPLATE_OPS_SKIPPED, MessageSender, get_sender, mask_phone,
)
from app.subscriptions import cancellation, jobs
from app.subscriptions.service import advance_cycle, delivery_date
from app.timeutil import fmt_day, local_date, now_utc

log = logging.getLogger(__name__)

RESTOCK_MAX = 10_000
STOCK_MAX = 1_000_000
NOTE_MAX = 500
STOCK_STATUSES = ("short", "low", "ok")
SUBSCRIPTION_FILTER_STATUSES = ("ACTIVE", "PAUSED")
PAGE_SIZE_DEFAULT, PAGE_SIZE_MAX = 25, 100
SEARCH_MAX = 100
MOVEMENTS_LIMIT_DEFAULT, MOVEMENTS_LIMIT_MAX = 50, 200


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #
def _reject_unknown(data: Any, allowed: set[str]) -> dict:
    if not isinstance(data, dict):
        raise ValidationError("Send a JSON object")
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ValidationError(f"Unknown field(s): {', '.join(unknown)}", details={"fields": unknown})
    return data


def _int(name: str, value: Any, lo: int, hi: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
        raise ValidationError(f"{name} must be a whole number from {lo} to {hi}", details={"field": name})
    return value


def _text(name: str, value: Any, required: bool) -> str | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise ValidationError(f"{name} is required", details={"field": name})
        return None
    if not isinstance(value, str) or len(value.strip()) > NOTE_MAX:
        raise ValidationError(f"{name} must be text of at most {NOTE_MAX} characters", details={"field": name})
    return value.strip()


def _product(session: Session, product_id: str) -> Product:
    product = session.get(Product, product_id)
    if product is None:
        raise NotFound("Product not found")
    return product


def _iso(v: dt.datetime | None) -> str | None:
    return v.isoformat() if v else None


# --------------------------------------------------------------------------- #
# Stock changes
# --------------------------------------------------------------------------- #
def restock(session: Session, product_id: str, data: Any) -> dict[str, Any]:
    """Add stock: one atomic upsert (creates the inventory row if missing) + a RESTOCK movement."""
    data = _reject_unknown(data, {"quantity", "note"})
    qty = _int("quantity", data.get("quantity"), 1, RESTOCK_MAX)
    note = _text("note", data.get("note"), required=False)
    _product(session, product_id)
    stmt = insert(Inventory).values(product_id=product_id, available_qty=qty)
    stmt = stmt.on_conflict_do_update(
        index_elements=[Inventory.product_id],
        set_={"available_qty": Inventory.available_qty + stmt.excluded.available_qty, "updated_at": func.now()},
    ).returning(Inventory.available_qty)
    new_qty = session.execute(stmt).scalar_one()
    session.add(InventoryMovement(product_id=product_id, delta=qty, reason="RESTOCK", note=note))
    session.flush()
    return {"product_id": product_id, "available_qty": new_qty, "added": qty}


def adjust(session: Session, product_id: str, data: Any) -> dict[str, Any]:
    """Set an absolute count (stock take, damage): lock the row, log ADJUST with delta = new - old."""
    data = _reject_unknown(data, {"available_qty", "reason"})
    new_qty = _int("available_qty", data.get("available_qty"), 0, STOCK_MAX)
    reason = _text("reason", data.get("reason"), required=True)
    _product(session, product_id)
    row = session.scalars(select(Inventory).where(Inventory.product_id == product_id)
                          .with_for_update()).one_or_none()
    if row is None:
        row = Inventory(product_id=product_id, available_qty=0)
        session.add(row)
        session.flush()
    old_qty = row.available_qty
    delta = new_qty - old_qty
    if delta:
        row.available_qty = new_qty
        row.updated_at = func.now()
        session.add(InventoryMovement(product_id=product_id, delta=delta, reason="ADJUST", note=reason))
    session.flush()
    return {"product_id": product_id, "available_qty": new_qty, "previous_qty": old_qty, "delta": delta}


def release_after_restock(engine: Engine, product_id: str) -> dict[str, Any]:
    """After a committed stock increase: send the reminder for held deliveries of this product
    that the stock now covers. Best effort: the restock already happened, so a failure here is
    reported, not raised (the next scheduler run releases them anyway)."""
    try:
        r = jobs.release_holds(engine, product_id)
    except Exception:
        log.exception("Releasing held deliveries of %s failed", product_id)
        return {"status": "failed", "released": 0, "out_of_stock": 0}
    return {"status": r["status"], "released": len(r["released"]), "out_of_stock": len(r["out_of_stock"])}


# --------------------------------------------------------------------------- #
# Delay or skip a delivery (operational reasons)
# --------------------------------------------------------------------------- #
# Deliveries nobody has approved yet: held, reminded (awaiting a reply), delayed, or the
# subscription's upcoming one with no occurrence yet. Approved orders are out of scope.
OPEN_STATUSES = ("SCHEDULED", "STOCK_HOLD", "NOTIFIED", "DELAYED")


def _options(sub: Subscription, used_delay_days: int) -> list[int]:
    """Same choices and limits as the customer's "Delay N days" (svc.delay_options)."""
    return [n for n in C.DELAY_OPTIONS_DAYS
            if used_delay_days + n <= C.MAX_TOTAL_DELAY_DAYS and used_delay_days + n < sub.frequency_days]


def upcoming(sub: Subscription, occ: DeliveryOccurrence | None, today: dt.date) -> dict[str, Any] | None:
    """What the admin can act on for this subscription: its open delivery (or the upcoming
    one), with the delay choices left. None when there is nothing to delay or skip."""
    if sub.status != "ACTIVE":
        return None
    if occ is not None:
        when, used = delivery_date(occ), occ.delay_days or 0
    elif sub.next_delivery_date is not None:
        when, used = sub.next_delivery_date, 0
    else:
        return None
    if when <= today:                                     # too late: the scheduler settles it
        return None
    return {"delivery_date": when.isoformat(), "status": occ.status if occ else "UPCOMING",
            "delay_options": _options(sub, used)}


def _open_delivery(session: Session, subscription_id: Any, today: dt.date
                   ) -> tuple[Subscription, DeliveryOccurrence | None, dt.date]:
    """Lock the subscription, then its open delivery (same order as the scheduler and the
    customer actions). Returns (subscription, open occurrence or None, delivery date)."""
    sid = _int("subscription_id", subscription_id, 1, 2**63 - 1)
    sub = session.scalars(select(Subscription).where(Subscription.subscription_id == sid)
                          .with_for_update().execution_options(populate_existing=True)).one_or_none()
    if sub is None:
        raise NotFound("Subscription not found")
    if sub.status != "ACTIVE":
        raise Conflict("Only active subscriptions have a delivery to delay or skip", code="not_active")
    if cancellation.pending_request(session, sid) is not None:
        raise Conflict("A cancellation request is waiting for the customer; withdraw it first",
                       code="cancel_requested")
    occ = session.scalars(select(DeliveryOccurrence)
                          .where(DeliveryOccurrence.subscription_id == sid,
                                 DeliveryOccurrence.status.in_(OPEN_STATUSES))
                          .order_by(DeliveryOccurrence.scheduled_date).limit(1)
                          .with_for_update().execution_options(populate_existing=True)).one_or_none()
    if occ is None:
        d = sub.next_delivery_date
        if d is None:
            raise Conflict("This subscription has no upcoming delivery", code="no_upcoming_delivery")
        if session.scalar(select(DeliveryOccurrence.occurrence_id).where(
                DeliveryOccurrence.subscription_id == sid, DeliveryOccurrence.scheduled_date == d)):
            raise Conflict(f"The delivery on {d.isoformat()} has already been settled", code="delivery_finalised")
    else:
        d = delivery_date(occ)
    if d <= today:
        raise Conflict(f"The delivery on {d.isoformat()} is today or past and can't be changed",
                       code="delivery_finalised")
    return sub, occ, d


def delay_delivery(session: Session, subscription_id: Any, data: Any, now: dt.datetime | None = None,
                   sender: MessageSender | None = None) -> dict[str, Any]:
    """Move the open (or upcoming) delivery by 1-2 days for operational reasons. It becomes
    DELAYED: the scheduler sends a fresh reminder before the new date, which the customer must
    approve. The subscription's cycle doesn't move. The customer is told why."""
    now = now or now_utc()
    data = _reject_unknown(data, {"days"})
    days = _int("days", data.get("days"), 1, C.MAX_TOTAL_DELAY_DAYS)
    sub, occ, old = _open_delivery(session, subscription_id, local_date(now))
    options = _options(sub, (occ.delay_days or 0) if occ else 0)
    if days not in options:
        raise Conflict(f"This delivery can't be delayed by {days} day(s)", code="delay_not_possible",
                       details={"options": options})
    was = occ.status if occ else "UPCOMING"
    if occ is None:
        occ = DeliveryOccurrence(subscription_id=sub.subscription_id, scheduled_date=old)
        session.add(occ)
    occ.delay_days = (occ.delay_days or 0) + days
    occ.status = "DELAYED"
    occ.response_deadline = occ.hold_until = None
    session.flush()
    new = delivery_date(occ)
    sent = (sender or get_sender()).send_template(
        session, customer=sub.customer, template=TEMPLATE_OPS_DELAYED, occurrence=occ,
        params={"product": sub.product.product_name, "date": fmt_day(old), "new_date": fmt_day(new)})
    log.info("Admin delayed subscription %s delivery %s -> %s (was %s)", sub.subscription_id, old, new, was)
    return {"subscription_id": sub.subscription_id, "occurrence_id": occ.occurrence_id, "action": "delayed",
            "previous_status": was, "delivery_date": old.isoformat(), "new_delivery_date": new.isoformat(),
            "product_id": sub.product_id, "customer_notified": sent is not None}


def skip_delivery(session: Session, subscription_id: Any, data: Any, now: dt.datetime | None = None,
                  sender: MessageSender | None = None) -> dict[str, Any]:
    """Skip the open (or upcoming) delivery for operational reasons: SKIPPED / OPERATIONAL, the
    subscription moves to its next cycle, and the customer is told why. Nothing is charged."""
    now = now or now_utc()
    today = local_date(now)
    _reject_unknown(data, set())
    sub, occ, when = _open_delivery(session, subscription_id, today)
    was = occ.status if occ else "UPCOMING"
    if occ is None:
        occ = DeliveryOccurrence(subscription_id=sub.subscription_id, scheduled_date=when)
        session.add(occ)
    occ.status, occ.skip_reason = "SKIPPED", "OPERATIONAL"
    occ.response_deadline = occ.hold_until = None
    session.flush()
    advance_cycle(session, sub, occ, today)
    session.flush()
    sent = (sender or get_sender()).send_template(
        session, customer=sub.customer, template=TEMPLATE_OPS_SKIPPED, occurrence=occ,
        params={"product": sub.product.product_name, "date": fmt_day(when),
                "next_date": fmt_day(sub.next_delivery_date)})
    log.info("Admin skipped subscription %s delivery %s (was %s)", sub.subscription_id, when, was)
    return {"subscription_id": sub.subscription_id, "occurrence_id": occ.occurrence_id, "action": "skipped",
            "previous_status": was, "delivery_date": when.isoformat(),
            "next_delivery_date": sub.next_delivery_date.isoformat() if sub.next_delivery_date else None,
            "product_id": sub.product_id, "customer_notified": sent is not None}


# --------------------------------------------------------------------------- #
# Cancellation requests (app/subscriptions/cancellation.py)
# --------------------------------------------------------------------------- #
def request_cancellation(session: Session, subscription_id: Any, data: Any, now: dt.datetime | None = None,
                         sender: MessageSender | None = None) -> dict[str, Any]:
    """Ask the customer to confirm cancelling (Telegram + dashboard)."""
    _reject_unknown(data, set())
    sid = _int("subscription_id", subscription_id, 1, 2**63 - 1)
    return cancellation.request_cancellation(session, sid, now=now or now_utc(), sender=sender)


def withdraw_cancellation(session: Session, subscription_id: Any, data: Any,
                          now: dt.datetime | None = None) -> dict[str, Any]:
    _reject_unknown(data, set())
    sid = _int("subscription_id", subscription_id, 1, 2**63 - 1)
    return cancellation.withdraw_request(session, sid, now=now or now_utc())


# --------------------------------------------------------------------------- #
# History
# --------------------------------------------------------------------------- #
def movements(session: Session, product_id: str, limit: Any = None) -> dict[str, Any]:
    if limit is None:
        limit = MOVEMENTS_LIMIT_DEFAULT
    else:
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            raise ValidationError("limit must be a whole number", details={"field": "limit"}) from None
        limit = _int("limit", limit, 1, MOVEMENTS_LIMIT_MAX)
    _product(session, product_id)
    rows = session.scalars(select(InventoryMovement).where(InventoryMovement.product_id == product_id)
                           .order_by(InventoryMovement.created_at.desc(), InventoryMovement.id.desc())
                           .limit(limit)).all()
    return {"product_id": product_id, "movements": [
        {"id": m.id, "delta": m.delta, "reason": m.reason, "occurrence_id": m.occurrence_id,
         "note": m.note, "created_at": _iso(m.created_at)} for m in rows]}


# --------------------------------------------------------------------------- #
# Shared pieces
# --------------------------------------------------------------------------- #
def _product_dict(p: Product) -> dict[str, Any]:
    return {"product_id": p.product_id, "product_name": p.product_name, "brand": p.brand,
            "pack_size": p.pack_size, "category": p.category, "image_url": p.image_url}


def _customer_dict(c: Customer) -> dict[str, Any]:
    return {"customer_id": c.customer_id, "customer_name": c.customer_name, "phone": mask_phone(c.phone_e164)}


def _query_int(name: str, raw: Any, default: int, lo: int, hi: int) -> int:
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise ValidationError(f"{name} must be a whole number", details={"field": name}) from None
    return _int(name, value, lo, hi)


def _search(raw: Any) -> str | None:
    q = (raw or "").strip()
    if len(q) > SEARCH_MAX:
        raise ValidationError(f"q must be at most {SEARCH_MAX} characters", details={"field": "q"})
    return q or None


def _like(q: str) -> str:
    """ILIKE pattern matching q literally (%, _ and \\ in the search are not wildcards)."""
    return "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _demand_by_product(session: Session) -> dict[str, dict[str, int]]:
    """Per product: units promised to reminders awaiting a reply, and units in held deliveries
    (ACTIVE subscriptions only, as the scheduler counts them)."""
    rows = session.execute(
        select(Subscription.product_id, DeliveryOccurrence.status, func.sum(Subscription.quantity))
        .join(DeliveryOccurrence, DeliveryOccurrence.subscription_id == Subscription.subscription_id)
        .where(Subscription.status == "ACTIVE", DeliveryOccurrence.status.in_(("NOTIFIED", "STOCK_HOLD")))
        .group_by(Subscription.product_id, DeliveryOccurrence.status)).all()
    out: dict[str, dict[str, int]] = {}
    for product_id, status, qty in rows:
        out.setdefault(product_id, {"promised": 0, "held": 0})["promised" if status == "NOTIFIED" else "held"] = int(qty)
    return out


# --------------------------------------------------------------------------- #
# At risk: deliveries held for stock
# --------------------------------------------------------------------------- #
def at_risk(session: Session, now: dt.datetime | None = None) -> dict[str, Any]:
    """Held deliveries (STOCK_HOLD) grouped by product, most urgent product first (earliest
    hold_until). Per product: stock, what's already promised to open reminders, what the holds
    need, and how much to add to release them all."""
    now = now or now_utc()
    rows = session.execute(
        select(DeliveryOccurrence, Subscription, Customer, Product)
        .join(Subscription, DeliveryOccurrence.subscription_id == Subscription.subscription_id)
        .join(Customer, Subscription.customer_id == Customer.customer_id)
        .join(Product, Subscription.product_id == Product.product_id)
        .where(DeliveryOccurrence.status == "STOCK_HOLD", Subscription.status == "ACTIVE")).all()
    stock = dict(session.execute(select(Inventory.product_id, Inventory.available_qty)).all())
    demand = _demand_by_product(session)

    groups: dict[str, dict[str, Any]] = {}
    for occ, sub, customer, product in rows:
        g = groups.setdefault(product.product_id, {"product": _product_dict(product), "lines": []})
        g["lines"].append({
            "occurrence_id": occ.occurrence_id, "subscription_id": sub.subscription_id,
            "delivery_date": delivery_date(occ).isoformat(), "quantity": sub.quantity,
            "customer": _customer_dict(customer), "preferred_slot": sub.preferred_slot,
            "held_since": _iso(occ.created_at), "hold_until": _iso(occ.hold_until),
            "expired": occ.hold_until is None or occ.hold_until <= now,
            "delay_options": _options(sub, occ.delay_days or 0),
        })

    products = []
    for product_id, g in groups.items():
        g["lines"].sort(key=lambda l: (l["delivery_date"], l["subscription_id"]))
        on_hand = stock.get(product_id, 0)
        promised = demand.get(product_id, {}).get("promised", 0)
        held = sum(l["quantity"] for l in g["lines"])
        free = on_hand - promised
        products.append({
            **g, "stock_now": on_hand, "promised": promised, "free_stock": free, "held_qty": held,
            "add_to_release_all": max(0, held - free),
            "earliest_hold_until": min(l["hold_until"] for l in g["lines"]),
            "earliest_delivery_date": g["lines"][0]["delivery_date"],
        })
    products.sort(key=lambda p: (p["earliest_hold_until"], p["product"]["product_name"]))
    return {"total": len(rows), "products": products}


# --------------------------------------------------------------------------- #
# Inventory list
# --------------------------------------------------------------------------- #
def _stock_status(left: int) -> str:
    if left < 0:
        return "short"
    return "low" if left < C.LOW_STOCK_BUFFER else "ok"


def inventory(session: Session, q: Any = None, status: Any = None) -> dict[str, Any]:
    """Every product with its stock, open demand (promised to reminders + held) and a status:
    short (holds the stock can't release), low (less than LOW_STOCK_BUFFER left), ok."""
    q = _search(q)
    if status not in (None, "") and status not in STOCK_STATUSES:
        raise ValidationError(f"status must be one of {', '.join(STOCK_STATUSES)}", details={"field": "status"})
    query = select(Product, Inventory).outerjoin(Inventory, Inventory.product_id == Product.product_id)
    if q:
        pattern = _like(q)
        query = query.where(or_(Product.product_name.ilike(pattern, escape="\\"),
                                Product.brand.ilike(pattern, escape="\\"),
                                Product.product_id.ilike(pattern, escape="\\")))
    demand = _demand_by_product(session)
    subs = dict(session.execute(select(Subscription.product_id, func.count())
                                .where(Subscription.status == "ACTIVE").group_by(Subscription.product_id)).all())
    items = []
    for product, inv in session.execute(query).all():
        on_hand = inv.available_qty if inv is not None else 0
        d = demand.get(product.product_id, {"promised": 0, "held": 0})
        left = on_hand - d["promised"] - d["held"]
        items.append({"product": _product_dict(product), "stock_now": on_hand, "promised": d["promised"],
                      "held_qty": d["held"], "left_after_demand": left, "status": _stock_status(left),
                      "active_subscriptions": subs.get(product.product_id, 0),
                      "updated_at": _iso(inv.updated_at) if inv is not None else None})
    counts = {s_: sum(1 for i in items if i["status"] == s_) for s_ in STOCK_STATUSES}
    if status:
        items = [i for i in items if i["status"] == status]
    rank = {s_: n for n, s_ in enumerate(STOCK_STATUSES)}
    items.sort(key=lambda i: (rank[i["status"]], i["left_after_demand"], i["product"]["product_name"]))
    return {"items": items, "counts": counts}


# --------------------------------------------------------------------------- #
# Subscriptions search (read-only)
# --------------------------------------------------------------------------- #
def search_subscriptions(session: Session, args: dict[str, Any]) -> dict[str, Any]:
    """ACTIVE and PAUSED subscriptions, filtered by q (product name/brand/id, customer id/name,
    phone digits), product_id, customer_id and status; paginated."""
    q = _search(args.get("q"))
    status = args.get("status") or None
    if status is not None and status not in SUBSCRIPTION_FILTER_STATUSES:
        raise ValidationError("status must be ACTIVE or PAUSED", details={"field": "status"})
    page = _query_int("page", args.get("page"), 1, 1, 100_000)
    page_size = _query_int("page_size", args.get("page_size"), PAGE_SIZE_DEFAULT, 1, PAGE_SIZE_MAX)

    where = [Subscription.status.in_([status] if status else SUBSCRIPTION_FILTER_STATUSES)]
    if args.get("product_id"):
        where.append(Subscription.product_id == args["product_id"])
    if args.get("customer_id"):
        where.append(Subscription.customer_id == args["customer_id"])
    if q:
        pattern = _like(q)
        matches = [col.ilike(pattern, escape="\\") for col in (
            Product.product_name, Product.brand, Product.product_id, Customer.customer_id, Customer.customer_name)]
        digits = "".join(ch for ch in q if ch.isdigit())
        if len(digits) >= 3 and digits == q.replace(" ", "").lstrip("+"):     # a phone number (part)
            matches.append(Customer.phone_e164.like("%" + digits + "%"))
        where.append(or_(*matches))

    base = (select(Subscription, Customer, Product)
            .join(Customer, Subscription.customer_id == Customer.customer_id)
            .join(Product, Subscription.product_id == Product.product_id).where(*where))
    total = session.scalar(select(func.count()).select_from(base.subquery()))
    rows = session.execute(base.order_by(Subscription.next_delivery_date.asc().nulls_last(),
                                         Subscription.subscription_id)
                           .offset((page - 1) * page_size).limit(page_size)).all()

    ids = [sub.subscription_id for sub, _, _ in rows]
    open_occ: dict[int, DeliveryOccurrence] = {}      # the open delivery per subscription (earliest)
    if ids:
        for occ in session.scalars(
                select(DeliveryOccurrence)
                .where(DeliveryOccurrence.subscription_id.in_(ids), DeliveryOccurrence.status.in_(OPEN_STATUSES))
                .order_by(DeliveryOccurrence.scheduled_date.desc())):
            open_occ[occ.subscription_id] = occ
    today = local_date(now_utc())
    requests: dict[int, CancellationRequest] = {}     # latest cancellation request per subscription
    if ids:
        for req in session.scalars(select(CancellationRequest).where(CancellationRequest.subscription_id.in_(ids))
                                   .order_by(CancellationRequest.requested_at, CancellationRequest.id)):
            requests[req.subscription_id] = req
    items = []
    for sub, customer, product in rows:
        current = product.discount_price
        occ = open_occ.get(sub.subscription_id)
        req = requests.get(sub.subscription_id)
        pending = req is not None and req.status == "PENDING"
        open_status = occ.status if occ is not None and occ.status != "SCHEDULED" else None
        items.append({
            "subscription_id": sub.subscription_id, "status": sub.status,
            "customer": _customer_dict(customer), "product": _product_dict(product),
            "quantity": sub.quantity, "frequency_days": sub.frequency_days,
            "next_delivery_date": sub.next_delivery_date.isoformat() if sub.next_delivery_date else None,
            "paused_until": sub.paused_until.isoformat() if sub.paused_until else None,
            "preferred_slot": sub.preferred_slot,
            "unit_price_at_subscription": f"{sub.unit_price_at_subscription:.2f}",
            "current_price": f"{current:.2f}", "price_changed": current != sub.unit_price_at_subscription,
            "open_delivery": open_status,                 # STOCK_HOLD | NOTIFIED | DELAYED | None
            "at_risk": open_status == "STOCK_HOLD",
            "upcoming": None if pending else upcoming(sub, occ, today),   # what Delay / Skip act on
            "cancel_request": cancellation.request_to_dict(req),          # latest, any status, or None
        })
    return {"items": items, "total": total, "page": page, "page_size": page_size}
