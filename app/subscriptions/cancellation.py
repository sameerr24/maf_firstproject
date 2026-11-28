"""Cancellation requests: the admin asks a customer to confirm cancelling a subscription.

- The admin requests it (admin panel). Unapproved open deliveries (held, reminded, delayed)
  are cancelled and no new reminders go out while the request is pending, so nobody is asked
  to pay for a subscription that may end. The customer gets the request on Telegram (Yes,
  cancel it / Keep my subscription) and sees the same question on the dashboard.
- The customer answers on either channel; the first answer wins (the subscription row is
  locked, then the request). Yes: the normal cancellation. Keep: the subscription carries on
  and reminders resume (a date missed meanwhile moves to the next cycle, as usual).
- The admin can withdraw a pending request. A normal cancel by the customer while a request
  is pending counts as confirming it.
- No timeout: a request stays pending until answered or withdrawn.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import Conflict, NotFound
from app.models import CancellationRequest, Subscription
from app.notifications.sender import TEMPLATE_CANCEL_REQUEST, MessageSender, get_sender
from app.subscriptions import service as svc
from app.timeutil import local_date, now_utc

CANCELLABLE_STATUSES = ("ACTIVE", "PAUSED")


def pending_request(session: Session, subscription_id: int, *, lock: bool = False) -> CancellationRequest | None:
    q = select(CancellationRequest).where(CancellationRequest.subscription_id == subscription_id,
                                          CancellationRequest.status == "PENDING")
    if lock:
        q = q.with_for_update().execution_options(populate_existing=True)
    return session.scalars(q).one_or_none()


def latest_request(session: Session, subscription_id: int) -> CancellationRequest | None:
    return session.scalars(select(CancellationRequest)
                           .where(CancellationRequest.subscription_id == subscription_id)
                           .order_by(CancellationRequest.requested_at.desc(), CancellationRequest.id.desc())
                           .limit(1)).first()


def request_to_dict(req: CancellationRequest | None) -> dict[str, Any] | None:
    if req is None:
        return None
    return {"request_id": req.id, "status": req.status, "requested_at": req.requested_at.isoformat(),
            "responded_at": req.responded_at.isoformat() if req.responded_at else None, "channel": req.channel}


def frequency_text(days: int) -> str:
    return "every day" if days == 1 else ("every week" if days == 7 else f"every {days} days")


def _lock_subscription(session: Session, subscription_id: int) -> Subscription:
    sub = session.scalars(select(Subscription).where(Subscription.subscription_id == subscription_id)
                          .with_for_update().execution_options(populate_existing=True)).one_or_none()
    if sub is None:
        raise NotFound("Subscription not found")
    return sub


# --------------------------------------------------------------------------- #
# Admin
# --------------------------------------------------------------------------- #
def request_cancellation(session: Session, subscription_id: int, now: dt.datetime | None = None,
                         sender: MessageSender | None = None) -> dict[str, Any]:
    """Ask the customer to confirm cancelling. Cancels unapproved open deliveries and sends the
    request on Telegram. Returns what was cancelled so the caller can tidy the chat and free stock."""
    now = now or now_utc()
    today = local_date(now)
    sub = _lock_subscription(session, subscription_id)
    if sub.status not in CANCELLABLE_STATUSES:
        raise Conflict("This subscription is already cancelled", code="not_active")
    if pending_request(session, sub.subscription_id, lock=True) is not None:
        raise Conflict("A cancellation request is already waiting for the customer", code="already_requested")

    open_deliveries = svc._pending_occurrences(session, sub)
    cancelled = [{"occurrence_id": o.occurrence_id, "status": o.status} for o in open_deliveries]
    for occ in open_deliveries:
        occ.status = "CANCELLED"
        occ.response_deadline = occ.hold_until = None
    session.flush()
    if sub.status == "ACTIVE" and sub.next_delivery_date is not None and open_deliveries:
        # the cancelled delivery's date is taken: if the customer keeps it, the next cycle follows
        sub.next_delivery_date = svc.next_free_date(session, sub, sub.next_delivery_date, today)

    req = CancellationRequest(subscription_id=sub.subscription_id, status="PENDING", requested_at=now)
    session.add(req)
    session.flush()
    sent = (sender or get_sender()).send_template(
        session, customer=sub.customer, template=TEMPLATE_CANCEL_REQUEST,
        params={"product": sub.product.product_name, "quantity": sub.quantity,
                "frequency": frequency_text(sub.frequency_days)},
        buttons=[f"CXL_YES:{req.id}", f"CXL_NO:{req.id}"])
    return {"request": request_to_dict(req), "subscription_id": sub.subscription_id,
            "customer_id": sub.customer_id, "product_id": sub.product_id,
            "product_name": sub.product.product_name, "cancelled_deliveries": cancelled,
            "customer_notified": sent is not None}


def withdraw_request(session: Session, subscription_id: int, now: dt.datetime | None = None) -> dict[str, Any]:
    """The admin no longer needs to cancel: the subscription carries on, reminders resume."""
    now = now or now_utc()
    sub = _lock_subscription(session, subscription_id)
    req = pending_request(session, sub.subscription_id, lock=True)
    if req is None:
        raise Conflict("There is no pending cancellation request for this subscription", code="no_pending_request")
    req.status, req.responded_at, req.channel = "WITHDRAWN", now, "admin"
    session.flush()
    return {"request": request_to_dict(req), "subscription_id": sub.subscription_id,
            "customer_id": sub.customer_id, "product_name": sub.product.product_name}


# --------------------------------------------------------------------------- #
# Customer
# --------------------------------------------------------------------------- #
def answer(session: Session, customer_id: str, *, confirm: bool, channel: str,
           request_id: int | None = None, subscription_id: int | None = None,
           now: dt.datetime | None = None) -> dict[str, Any]:
    """The customer's answer, by request (Telegram button) or by subscription (dashboard).
    Locks the subscription, then the request, so the first answer wins on any channel.
    Returns {"outcome": "cancelled" | "kept", "request", "subscription"}; a request that is no
    longer pending raises Conflict (request_closed) saying what happened to it."""
    now = now or now_utc()
    if request_id is not None:
        row = session.execute(select(CancellationRequest.subscription_id).join(Subscription).where(
            CancellationRequest.id == request_id, Subscription.customer_id == customer_id)).first()
        if row is None:
            raise NotFound("Cancellation request not found")
        subscription_id = row.subscription_id
    sub = svc._get_owned(session, customer_id, subscription_id, lock=True)
    q = select(CancellationRequest).where(CancellationRequest.subscription_id == sub.subscription_id)
    q = q.where(CancellationRequest.id == request_id) if request_id is not None \
        else q.where(CancellationRequest.status == "PENDING")
    req = session.scalars(q.with_for_update().execution_options(populate_existing=True)).one_or_none()
    if req is None or req.status != "PENDING":
        raise Conflict(_closed_message(req, sub), code="request_closed")

    if confirm:
        svc.cancel_subscription(session, customer_id, sub.subscription_id)   # marks the request CONFIRMED
        req.status, req.channel, req.responded_at = "CONFIRMED", channel, now
        outcome = "cancelled"
    else:
        req.status, req.responded_at, req.channel = "DECLINED", now, channel
        outcome = "kept"
    session.flush()
    session.refresh(sub)
    return {"outcome": outcome, "request": request_to_dict(req), "subscription": svc.subscription_to_dict(sub)}


def _closed_message(req: CancellationRequest | None, sub: Subscription) -> str:
    product = sub.product.product_name
    if req is None or req.status == "WITHDRAWN":
        return f"We no longer need to cancel your {product} subscription. It continues as before."
    if req.status == "CONFIRMED":
        return f"Your {product} subscription is already cancelled."
    return f"You already chose to keep your {product} subscription."


# --------------------------------------------------------------------------- #
# Chat texts (shared by the bot and the website, like the delivery confirmations)
# --------------------------------------------------------------------------- #
def cancelled_text(product: str) -> str:
    return f"Your {product} subscription is cancelled, as requested. You won't be charged again."


def kept_text(product: str, next_date: str | None) -> str:
    text = f"Thanks, your {product} subscription continues."
    return text + (f" Next delivery: {next_date}; we'll send you a reminder before then." if next_date else "")


def withdrawn_text(product: str) -> str:
    return f"Good news: we no longer need to cancel your {product} subscription. It continues as before."

