"""Scheduler jobs for subscriptions. run_scheduler() runs them all, in this order:

1. Resume: PAUSED subscriptions whose paused_until has been reached become ACTIVE again.
2. Auto-skip (job B): occurrences still NOTIFIED after their response_deadline become
   SKIPPED / NO_RESPONSE; the customer gets the delivery_skipped_notice template and the
   subscription moves to its next cycle.
3. Remind (job A): ACTIVE subscriptions whose next delivery is at most REMINDER_DAYS_BEFORE
   days away and that have no occurrence for that date yet get one:
   - customer no longer reachable on WhatsApp (opted out / phone removed after subscribing):
     SKIPPED / NO_RESPONSE right away, no message, next cycle
   - stock below the subscription quantity (soft check, no reservation): OUT_OF_STOCK,
     out-of-stock notice, next cycle
   - otherwise NOTIFIED with a response_deadline, and the reminder template with
     APPROVE/SKIP/STOP buttons
   - a delivery date that is already today or past (scheduler was down, time travel):
     no reminder, the subscription moves to its next schedulable cycle

Every subscription/occurrence is handled in its own transaction, so one failure doesn't
block the rest. A PostgreSQL advisory lock makes concurrent runs (several processes, the
dev endpoint) skip instead of doubling up; rows are also locked with SKIP LOCKED.

`now` can be any moment (the dev endpoint passes it for demo time travel); all business
dates derive from it, nothing reads the real clock.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any, Callable

from sqlalchemy import exists, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app import constants as C
from app.models import DeliveryOccurrence, Inventory, Subscription
from app.notifications.sender import (
    TEMPLATE_OUT_OF_STOCK, TEMPLATE_REMINDER, TEMPLATE_SKIPPED, MessageSender, can_message, get_sender,
)
from app.subscriptions.service import next_free_date, resume
from app.timeutil import LOCAL_TZ, local_date, now_utc

log = logging.getLogger(__name__)

ADVISORY_LOCK_KEY = 7420_1124  # arbitrary, unique to this job runner


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def response_deadline(delivery_date: dt.date, now: dt.datetime) -> dt.datetime:
    """RESPONSE_DEADLINE_LOCAL_TIME on the day before delivery, but never sooner than
    MIN_RESPONSE_WINDOW_MINUTES from now."""
    at = dt.time.fromisoformat(C.RESPONSE_DEADLINE_LOCAL_TIME)
    deadline = dt.datetime.combine(delivery_date - dt.timedelta(days=1), at, tzinfo=LOCAL_TZ)
    return max(deadline, now + dt.timedelta(minutes=C.MIN_RESPONSE_WINDOW_MINUTES)).astimezone(dt.timezone.utc)


def _fmt_date(d: dt.date) -> str:
    return d.strftime("%a, %d %b")          # "Sat, 28 Nov"


def _fmt_rupees(amount) -> str:
    return f"₹{amount:.2f}"


def _reminder_price(sub: Subscription) -> str:
    """Total for this delivery at today's subscriber price; flags a change since subscribing."""
    current = sub.product.discount_price
    text = _fmt_rupees(current * sub.quantity)
    if current != sub.unit_price_at_subscription:
        text += f" (price changed, was {_fmt_rupees(sub.unit_price_at_subscription * sub.quantity)})"
    return text


def _each(engine: Engine, ids: list, fn: Callable[[Session, Any], str | None], summary: dict) -> None:
    """Run fn(session, id) for every id in its own transaction; record outcomes and errors."""
    for item_id in ids:
        try:
            with Session(engine, expire_on_commit=False) as s, s.begin():
                outcome = fn(s, item_id)
        except Exception as e:  # keep going; one bad row must not stop the run
            log.exception("Scheduler: %s %s failed", fn.__name__, item_id)
            summary["errors"].append(f"{fn.__name__} {item_id}: {e}")
            continue
        if outcome:
            summary[outcome].append(item_id)


# --------------------------------------------------------------------------- #
# Jobs
# --------------------------------------------------------------------------- #
def _resume_due(engine: Engine, today: dt.date, summary: dict) -> None:
    with Session(engine) as s:
        ids = s.scalars(select(Subscription.subscription_id).where(
            Subscription.status == "PAUSED", Subscription.paused_until <= today)).all()

    def resume_subscription(s: Session, sid: int) -> str | None:
        sub = s.scalars(select(Subscription).where(
            Subscription.subscription_id == sid, Subscription.status == "PAUSED",
            Subscription.paused_until <= today).with_for_update(skip_locked=True)).one_or_none()
        if sub is None:
            return None
        resume(s, sub, today)
        return "resumed"

    _each(engine, list(ids), resume_subscription, summary)


def _auto_skip(engine: Engine, now: dt.datetime, today: dt.date, sender: MessageSender, summary: dict) -> None:
    with Session(engine) as s:
        rows = s.execute(select(DeliveryOccurrence.occurrence_id, DeliveryOccurrence.subscription_id).where(
            DeliveryOccurrence.status == "NOTIFIED", DeliveryOccurrence.response_deadline <= now)).all()

    def skip_unanswered(s: Session, row) -> str | None:
        occ_id, sub_id = row
        # Lock order matches the service layer: subscription first, then occurrence.
        sub = s.scalars(select(Subscription).where(Subscription.subscription_id == sub_id)
                        .with_for_update(skip_locked=True)).one_or_none()
        occ = s.scalars(select(DeliveryOccurrence).where(
            DeliveryOccurrence.occurrence_id == occ_id, DeliveryOccurrence.status == "NOTIFIED",
            DeliveryOccurrence.response_deadline <= now).with_for_update(skip_locked=True)).one_or_none()
        if sub is None or occ is None:
            return None
        occ.status = "SKIPPED"
        occ.skip_reason = "NO_RESPONSE"
        if sub.status == "ACTIVE" and sub.next_delivery_date == occ.scheduled_date:
            sub.next_delivery_date = next_free_date(
                s, sub, occ.scheduled_date + dt.timedelta(days=sub.frequency_days), today)
        sender.send_template(s, customer=sub.customer, template=TEMPLATE_SKIPPED, occurrence=occ,
                             params={"product": sub.product.product_name, "date": _fmt_date(occ.scheduled_date),
                                     "next_date": _fmt_date(sub.next_delivery_date)
                                     if sub.status == "ACTIVE" and sub.next_delivery_date else None})
        return "auto_skipped"

    _each(engine, list(rows), skip_unanswered, summary)


def _remind(engine: Engine, now: dt.datetime, today: dt.date, sender: MessageSender, summary: dict) -> None:
    horizon = today + dt.timedelta(days=C.REMINDER_DAYS_BEFORE)
    has_occurrence = exists().where(DeliveryOccurrence.subscription_id == Subscription.subscription_id,
                                    DeliveryOccurrence.scheduled_date == Subscription.next_delivery_date)
    with Session(engine) as s:
        ids = s.scalars(select(Subscription.subscription_id).where(
            Subscription.status == "ACTIVE", Subscription.next_delivery_date <= horizon, ~has_occurrence)
            .order_by(Subscription.next_delivery_date, Subscription.subscription_id)).all()

    def remind_subscription(s: Session, sid: int) -> str | None:
        sub = s.scalars(select(Subscription).where(
            Subscription.subscription_id == sid, Subscription.status == "ACTIVE")
            .with_for_update(skip_locked=True)).one_or_none()
        if sub is None or sub.next_delivery_date is None or sub.next_delivery_date > horizon:
            return None
        d = sub.next_delivery_date
        if s.scalar(select(DeliveryOccurrence.occurrence_id).where(
                DeliveryOccurrence.subscription_id == sid, DeliveryOccurrence.scheduled_date == d)):
            return None                                         # already handled; waiting for a reply

        if d <= today:                                          # too late to ask: move to the next cycle
            sub.next_delivery_date = next_free_date(s, sub, d, today)
            log.warning("Scheduler: subscription %s missed its %s delivery; next is %s", sid, d,
                        sub.next_delivery_date)
            return "missed"

        if not can_message(sub.customer):                      # can't ask, so it can't be approved
            s.add(DeliveryOccurrence(subscription_id=sid, scheduled_date=d, status="SKIPPED",
                                     skip_reason="NO_RESPONSE"))
            s.flush()
            sub.next_delivery_date = next_free_date(s, sub, d + dt.timedelta(days=sub.frequency_days), today)
            log.warning("Scheduler: subscription %s skipped %s: customer %s has no WhatsApp", sid, d,
                        sub.customer_id)
            return "no_whatsapp"

        params = {"product": sub.product.product_name, "quantity": sub.quantity, "date": _fmt_date(d)}
        stock = s.get(Inventory, sub.product_id)
        if stock is None or stock.available_qty < sub.quantity:
            occ = DeliveryOccurrence(subscription_id=sid, scheduled_date=d, status="OUT_OF_STOCK",
                                     skip_reason="OUT_OF_STOCK", notified_at=now)
            s.add(occ)
            s.flush()
            sub.next_delivery_date = next_free_date(s, sub, d + dt.timedelta(days=sub.frequency_days), today)
            sender.send_template(s, customer=sub.customer, template=TEMPLATE_OUT_OF_STOCK, occurrence=occ,
                                 params={**params, "next_date": _fmt_date(sub.next_delivery_date)})
            return "out_of_stock"

        occ = DeliveryOccurrence(subscription_id=sid, scheduled_date=d, status="NOTIFIED", notified_at=now,
                                 response_deadline=response_deadline(d, now))
        s.add(occ)
        s.flush()
        oid = occ.occurrence_id
        sender.send_template(s, customer=sub.customer, template=TEMPLATE_REMINDER, occurrence=occ,
                             params={**params, "price": _reminder_price(sub)},
                             buttons=[f"APPROVE:{oid}", f"SKIP:{oid}", f"STOP:{oid}"])
        return "reminded"

    _each(engine, list(ids), remind_subscription, summary)


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
def run_scheduler(engine: Engine, now: dt.datetime | None = None,
                  sender: MessageSender | None = None) -> dict[str, Any]:
    """Run all jobs once as of `now` (aware; default: the real clock). Returns a summary."""
    now = now or now_utc()
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    sender = sender or get_sender()
    today = local_date(now)
    summary: dict[str, Any] = {"now": now.isoformat(), "today": today.isoformat(), "status": "ok",
                               "resumed": [], "auto_skipped": [], "reminded": [], "out_of_stock": [],
                               "missed": [], "no_whatsapp": [], "errors": []}
    with engine.connect() as lock_conn:
        got_lock = lock_conn.scalar(select(func.pg_try_advisory_lock(ADVISORY_LOCK_KEY)))
        lock_conn.commit()                       # session-level lock outlives the transaction
        if not got_lock:
            summary["status"] = "busy"
            return summary
        try:
            _resume_due(engine, today, summary)
            _auto_skip(engine, now, today, sender, summary)
            _remind(engine, now, today, sender, summary)
        finally:
            lock_conn.scalar(select(func.pg_advisory_unlock(ADVISORY_LOCK_KEY)))
            lock_conn.commit()
    if summary["errors"]:
        summary["status"] = "partial"
    counts = {k: len(v) for k, v in summary.items() if isinstance(v, list)}
    log.info("Scheduler run as of %s: %s", now.isoformat(), counts)
    return summary
