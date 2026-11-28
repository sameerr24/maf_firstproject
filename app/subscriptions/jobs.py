"""Scheduler jobs for subscriptions. run_scheduler() runs them all, in this order:

1. Resume: PAUSED subscriptions whose paused_until has been reached become ACTIVE again.
2. Auto-skip (job B): occurrences still NOTIFIED after their response_deadline become
   SKIPPED / NO_RESPONSE; the customer gets the delivery_skipped_notice template and the
   subscription moves to its next cycle.
3. Stock holds: STOCK_HOLD occurrences (see below) whose hold_until has passed become
   OUT_OF_STOCK and the customer gets the out-of-stock notice (next cycle); the others are
   released, oldest delivery first, if the stock now covers them: they get the normal reminder.
   release_holds() does the same right after an admin restock.
4. Remind delayed: DELAYED occurrences (customer chose "Delay N days") whose new delivery
   date is at most REMINDER_DAYS_BEFORE days away get a fresh reminder, since every charge
   must be approved. Same outcomes as below; a new date already reached is skipped.
5. Remind (job A): ACTIVE subscriptions whose next delivery is at most REMINDER_DAYS_BEFORE
   days away and that have no occurrence for that date yet get one:
   - customer no longer reachable on WhatsApp (opted out / phone removed after subscribing):
     SKIPPED / NO_RESPONSE right away, no message, next cycle
   - stock can't cover it (soft check, no reservation; stock already promised to reminders
     awaiting a reply is counted): STOCK_HOLD until STOCK_HOLD_UNTIL_LOCAL_TIME the day before
     delivery, with no message, so the admin can restock. Too late to hold: OUT_OF_STOCK,
     out-of-stock notice, next cycle
   - otherwise NOTIFIED with a response_deadline, and the reminder template with
     APPROVE/SKIP/STOP buttons
   - a delivery date that is already today or past (scheduler was down, time travel):
     no reminder, the subscription moves to its next schedulable cycle
   Subscriptions with a pending cancellation request (app/subscriptions/cancellation.py) are
   left alone until the customer answers.

Every subscription/occurrence is handled in its own transaction, so one failure doesn't
block the rest. A PostgreSQL advisory lock makes concurrent runs (several processes, the
dev endpoint) skip instead of doubling up; rows are also locked with SKIP LOCKED.

`now` can be any moment (the dev endpoint passes it for demo time travel); all business
dates derive from it, nothing reads the real clock.
"""

from __future__ import annotations

import datetime as dt
import logging
from contextlib import contextmanager
from typing import Any, Callable, Iterator

from sqlalchemy import exists, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app import constants as C
from app.models import CancellationRequest, DeliveryOccurrence, Inventory, Subscription
from app.notifications.sender import (
    TEMPLATE_OUT_OF_STOCK, TEMPLATE_REMINDER, TEMPLATE_SKIPPED, MessageSender, can_message, get_sender,
)
from app.subscriptions.service import advance_cycle, delivery_date, next_free_date, resume
from app.timeutil import LOCAL_TZ, fmt_day, local_date, now_utc

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


def hold_until(delivery_date: dt.date) -> dt.datetime:
    """STOCK_HOLD_UNTIL_LOCAL_TIME on the day before delivery (UTC)."""
    at = dt.time.fromisoformat(C.STOCK_HOLD_UNTIL_LOCAL_TIME)
    return dt.datetime.combine(delivery_date - dt.timedelta(days=1), at, tzinfo=LOCAL_TZ).astimezone(dt.timezone.utc)


def promised_qty(s: Session, product_id: str, exclude_occurrence: int | None = None) -> int:
    """Units of this product promised to reminders awaiting a reply (they take stock if approved)."""
    q = (select(func.coalesce(func.sum(Subscription.quantity), 0))
         .join(DeliveryOccurrence, DeliveryOccurrence.subscription_id == Subscription.subscription_id)
         .where(Subscription.product_id == product_id, Subscription.status == "ACTIVE",
                DeliveryOccurrence.status == "NOTIFIED"))
    if exclude_occurrence is not None:
        q = q.where(DeliveryOccurrence.occurrence_id != exclude_occurrence)
    return int(s.scalar(q))


def stock_covers(s: Session, sub: Subscription, occ: DeliveryOccurrence) -> bool:
    """Soft check: free stock (on hand minus already promised) covers this delivery."""
    stock = s.get(Inventory, sub.product_id)
    on_hand = stock.available_qty if stock is not None else 0
    return on_hand - promised_qty(s, sub.product_id, occ.occurrence_id) >= sub.quantity


_fmt_date = fmt_day                          # "Sat, 28 Nov"


def _fmt_reply_by(deadline: dt.datetime) -> str:
    local = deadline.astimezone(LOCAL_TZ)                  # "9:00 PM on Fri, 27 Nov" (portable, no %-I)
    hour = local.hour % 12 or 12
    return f"{hour}:{local.minute:02d} {'AM' if local.hour < 12 else 'PM'} on {_fmt_date(local.date())}"


def _fmt_rupees(amount) -> str:
    return f"₹{amount:.2f}"


def _reminder_price(sub: Subscription) -> str:
    """Total for this delivery at today's subscriber price; flags a change since subscribing."""
    current = sub.product.discount_price
    text = _fmt_rupees(current * sub.quantity)
    if current != sub.unit_price_at_subscription:
        text += f" (price changed, was {_fmt_rupees(sub.unit_price_at_subscription * sub.quantity)})"
    return text


def _each(engine: Engine, items: list, fn: Callable[[Session, Any], str | None], summary: dict,
          record: Callable[[Any], Any] = lambda item: item) -> None:
    """Run fn(session, item) for every item in its own transaction; record outcomes and errors.
    Summary lists hold subscription ids (`record` maps an item to one)."""
    for item in items:
        try:
            with Session(engine, expire_on_commit=False) as s, s.begin():
                outcome = fn(s, item)
        except Exception as e:  # keep going; one bad row must not stop the run
            log.exception("Scheduler: %s %s failed", fn.__name__, item)
            summary["errors"].append(f"{fn.__name__} {item}: {e}")
            continue
        if outcome:
            summary[outcome].append(record(item))


_advance = advance_cycle


def _settle_or_remind(s: Session, sub: Subscription, occ: DeliveryOccurrence, now: dt.datetime,
                      today: dt.date, sender: MessageSender) -> str:
    """Decide a due delivery (new or delayed, already flushed): skip if the customer can't be
    asked on WhatsApp, hold it if the stock can't cover it (out of stock if it's too late to
    hold), or send the reminder."""
    d = delivery_date(occ)
    if not can_message(s, sub.customer):                  # can't ask, so it can't be approved
        occ.status, occ.skip_reason = "SKIPPED", "NO_RESPONSE"
        _advance(s, sub, occ, today)
        log.warning("Scheduler: subscription %s skipped %s: customer %s can't be messaged",
                    sub.subscription_id, d, sub.customer_id)
        return "no_whatsapp"

    if not stock_covers(s, sub, occ):
        until = hold_until(d)
        if until <= now:                                  # too late to wait for a restock
            return _out_of_stock(s, sub, occ, now, today, sender)
        occ.status, occ.hold_until = "STOCK_HOLD", until
        occ.notified_at = occ.response_deadline = None
        log.info("Scheduler: subscription %s delivery %s held for stock until %s", sub.subscription_id, d, until)
        return "held"

    return _send_reminder(s, sub, occ, now, sender)


def _out_of_stock(s: Session, sub: Subscription, occ: DeliveryOccurrence, now: dt.datetime,
                  today: dt.date, sender: MessageSender) -> str:
    occ.status, occ.skip_reason, occ.notified_at = "OUT_OF_STOCK", "OUT_OF_STOCK", now
    occ.response_deadline = None
    _advance(s, sub, occ, today)
    sender.send_template(s, customer=sub.customer, template=TEMPLATE_OUT_OF_STOCK, occurrence=occ,
                         params={"product": sub.product.product_name, "quantity": sub.quantity,
                                 "date": _fmt_date(delivery_date(occ)),
                                 "next_date": _fmt_date(sub.next_delivery_date)})
    return "out_of_stock"


def _send_reminder(s: Session, sub: Subscription, occ: DeliveryOccurrence, now: dt.datetime,
                   sender: MessageSender) -> str:
    d = delivery_date(occ)
    occ.status, occ.notified_at = "NOTIFIED", now
    occ.response_deadline = response_deadline(d, now)
    oid = occ.occurrence_id
    sender.send_template(s, customer=sub.customer, template=TEMPLATE_REMINDER, occurrence=occ,
                         params={"product": sub.product.product_name, "quantity": sub.quantity,
                                 "date": _fmt_date(d), "price": _reminder_price(sub),
                                 "reply_by": _fmt_reply_by(occ.response_deadline)},
                         buttons=[f"APPROVE:{oid}", f"SKIP:{oid}", f"STOP:{oid}"])
    return "reminded"


def _lock_sub_and_occ(s: Session, sub_id: int, occ_id: int, *conditions):
    """Lock order matches the service layer: subscription first, then occurrence."""
    sub = s.scalars(select(Subscription).where(Subscription.subscription_id == sub_id)
                    .with_for_update(skip_locked=True)).one_or_none()
    occ = s.scalars(select(DeliveryOccurrence).where(DeliveryOccurrence.occurrence_id == occ_id, *conditions)
                    .with_for_update(skip_locked=True)).one_or_none()
    return (sub, occ) if sub is not None and occ is not None else (None, None)


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
        sub, occ = _lock_sub_and_occ(s, sub_id, occ_id, DeliveryOccurrence.status == "NOTIFIED",
                                     DeliveryOccurrence.response_deadline <= now)
        if occ is None:
            return None
        occ.status, occ.skip_reason = "SKIPPED", "NO_RESPONSE"
        _advance(s, sub, occ, today)
        if sub.status == "ACTIVE" and sub.next_delivery_date:   # always true today (pause/cancel clear pending)
            sender.send_template(s, customer=sub.customer, template=TEMPLATE_SKIPPED, occurrence=occ,
                                 params={"product": sub.product.product_name,
                                         "date": _fmt_date(delivery_date(occ)),
                                         "next_date": _fmt_date(sub.next_delivery_date)})
        return "auto_skipped"

    _each(engine, [tuple(r) for r in rows], skip_unanswered, summary, record=lambda row: row[1])


def _process_holds(engine: Engine, now: dt.datetime, today: dt.date, sender: MessageSender, summary: dict,
                   product_id: str | None = None) -> None:
    """Held deliveries, oldest delivery first: past hold_until -> out of stock (customer told);
    stock now covers it -> the normal reminder ("released"); otherwise it stays held. One
    transaction each, so every release counts the stock promised by the ones before it."""
    q = select(DeliveryOccurrence).where(DeliveryOccurrence.status == "STOCK_HOLD")
    if product_id is not None:
        q = q.join(Subscription).where(Subscription.product_id == product_id)
    with Session(engine) as s:
        rows = [(o.occurrence_id, o.subscription_id) for o in
                sorted(s.scalars(q), key=lambda o: (delivery_date(o), o.subscription_id))]

    def settle_hold(s: Session, row) -> str | None:
        occ_id, sub_id = row
        sub, occ = _lock_sub_and_occ(s, sub_id, occ_id, DeliveryOccurrence.status == "STOCK_HOLD")
        if occ is None or sub.status != "ACTIVE":         # pause / cancel already cleared it
            return None
        if occ.hold_until is None or occ.hold_until <= now:
            return _out_of_stock(s, sub, occ, now, today, sender)
        if not stock_covers(s, sub, occ):
            return None                                   # still waiting for a restock
        outcome = _settle_or_remind(s, sub, occ, now, today, sender)
        return "released" if outcome == "reminded" else outcome

    _each(engine, rows, settle_hold, summary, record=lambda row: row[1])


def _remind_delayed(engine: Engine, now: dt.datetime, today: dt.date, sender: MessageSender,
                    summary: dict) -> None:
    horizon = today + dt.timedelta(days=C.REMINDER_DAYS_BEFORE)
    with Session(engine) as s:
        rows = [(o.occurrence_id, o.subscription_id) for o in s.scalars(
            select(DeliveryOccurrence).where(DeliveryOccurrence.status == "DELAYED"))
            if delivery_date(o) <= horizon]

    def remind_delayed(s: Session, row) -> str | None:
        occ_id, sub_id = row
        sub, occ = _lock_sub_and_occ(s, sub_id, occ_id, DeliveryOccurrence.status == "DELAYED")
        if occ is None or sub.status != "ACTIVE":
            return None
        if delivery_date(occ) <= today:                   # too late to ask again: this cycle is skipped
            occ.status, occ.skip_reason = "SKIPPED", "NO_RESPONSE"
            _advance(s, sub, occ, today)
            return "missed"
        return _settle_or_remind(s, sub, occ, now, today, sender)

    _each(engine, rows, remind_delayed, summary, record=lambda row: row[1])


def _remind(engine: Engine, now: dt.datetime, today: dt.date, sender: MessageSender, summary: dict) -> None:
    horizon = today + dt.timedelta(days=C.REMINDER_DAYS_BEFORE)
    has_occurrence = exists().where(DeliveryOccurrence.subscription_id == Subscription.subscription_id,
                                    DeliveryOccurrence.scheduled_date == Subscription.next_delivery_date)
    cancel_pending = exists().where(CancellationRequest.subscription_id == Subscription.subscription_id,
                                    CancellationRequest.status == "PENDING")
    with Session(engine) as s:
        ids = s.scalars(select(Subscription.subscription_id).where(
            Subscription.status == "ACTIVE", Subscription.next_delivery_date <= horizon, ~has_occurrence,
            ~cancel_pending)
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
        if s.scalar(select(CancellationRequest.id).where(
                CancellationRequest.subscription_id == sid, CancellationRequest.status == "PENDING")):
            return None                                         # waiting for the customer to confirm cancelling

        if d <= today:                                          # too late to ask: move to the next cycle
            sub.next_delivery_date = next_free_date(s, sub, d, today)
            log.warning("Scheduler: subscription %s missed its %s delivery; next is %s", sid, d,
                        sub.next_delivery_date)
            return "missed"

        occ = DeliveryOccurrence(subscription_id=sid, scheduled_date=d, status="SCHEDULED")
        s.add(occ)
        s.flush()
        return _settle_or_remind(s, sub, occ, now, today, sender)

    _each(engine, list(ids), remind_subscription, summary)


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
def _new_summary(now: dt.datetime, today: dt.date) -> dict[str, Any]:
    return {"now": now.isoformat(), "today": today.isoformat(), "status": "ok",
            "resumed": [], "auto_skipped": [], "released": [], "held": [], "reminded": [], "out_of_stock": [],
            "missed": [], "no_whatsapp": [], "errors": []}


@contextmanager
def _scheduler_lock(engine: Engine, wait: bool) -> Iterator[bool]:
    """The advisory lock that serialises scheduler runs and hold releases. wait=False: yield
    False at once if another run holds it."""
    with engine.connect() as lock_conn:
        if wait:
            lock_conn.execute(select(func.pg_advisory_lock(ADVISORY_LOCK_KEY)))
            got_lock = True
        else:
            got_lock = bool(lock_conn.scalar(select(func.pg_try_advisory_lock(ADVISORY_LOCK_KEY))))
        lock_conn.commit()                       # session-level lock outlives the transaction
        try:
            yield got_lock
        finally:
            if got_lock:
                lock_conn.scalar(select(func.pg_advisory_unlock(ADVISORY_LOCK_KEY)))
                lock_conn.commit()


def _finish(summary: dict[str, Any], label: str) -> dict[str, Any]:
    if summary["errors"]:
        summary["status"] = "partial"
    counts = {k: len(v) for k, v in summary.items() if isinstance(v, list)}
    log.info("%s as of %s: %s", label, summary["now"], counts)
    return summary


def _start(now: dt.datetime | None, sender: MessageSender | None):
    now = now or now_utc()
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return now, sender or get_sender(), local_date(now)


def run_scheduler(engine: Engine, now: dt.datetime | None = None,
                  sender: MessageSender | None = None) -> dict[str, Any]:
    """Run all jobs once as of `now` (aware; default: the real clock). Returns a summary whose
    lists hold subscription ids."""
    now, sender, today = _start(now, sender)
    summary = _new_summary(now, today)
    with _scheduler_lock(engine, wait=False) as got_lock:
        if not got_lock:
            summary["status"] = "busy"
            return summary
        _resume_due(engine, today, summary)
        _auto_skip(engine, now, today, sender, summary)
        _process_holds(engine, now, today, sender, summary)
        _remind_delayed(engine, now, today, sender, summary)
        _remind(engine, now, today, sender, summary)
    return _finish(summary, "Scheduler run")


def release_holds(engine: Engine, product_id: str | None = None, now: dt.datetime | None = None,
                  sender: MessageSender | None = None) -> dict[str, Any]:
    """Settle held deliveries right away (after a restock), for one product or all: release the
    ones the stock now covers (reminder sent), expire the ones past hold_until. Waits for a
    running scheduler pass instead of skipping, so a restock is never ignored."""
    now, sender, today = _start(now, sender)
    summary = _new_summary(now, today)
    with _scheduler_lock(engine, wait=True):
        _process_holds(engine, now, today, sender, summary, product_id=product_id)
    return _finish(summary, "Hold release")
