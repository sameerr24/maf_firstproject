from __future__ import annotations

import datetime as dt
import logging
import re
import uuid

from flask import current_app, g, jsonify, request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.auth import login_required
from app.db import db_session
from app.errors import Conflict, NotFound, ServiceError, ValidationError
from app.models import Customer, CustomerAutopayMandate
from app.payments import bp
from app.payments.razorpay import RazorpayAuthenticationError, RazorpayClient, RazorpayError
from app.subscriptions import service as subscription_service
from app import constants as C

log = logging.getLogger(__name__)


class GatewayUnavailable(ServiceError):
    status = 503
    code = "payment_gateway_unavailable"


def _client() -> RazorpayClient:
    key_id = current_app.config.get("RAZORPAY_KEY_ID", "")
    key_secret = current_app.config.get("RAZORPAY_KEY_SECRET", "")
    try:
        return RazorpayClient(key_id, key_secret)
    except ValueError as exc:
        raise GatewayUnavailable(
            "Razorpay Autopay setup is unavailable. Configure Razorpay test keys to continue.",
        ) from exc


def _configured() -> bool:
    key_id = current_app.config.get("RAZORPAY_KEY_ID", "")
    return (key_id.startswith("rzp_test_") and len(key_id) > len("rzp_test_")
            and bool(current_app.config.get("RAZORPAY_KEY_SECRET")))


def _public_status(mandate: CustomerAutopayMandate | None) -> dict:
    cap_paise = C.RAZORPAY_AUTOPAY_MAX_AMOUNT_PAISE
    return {
        "configured": _configured(),
        "status": mandate.status.lower() if mandate else "not_setup",
        "setup_later_allowed": not (mandate and mandate.status == "ACTIVE"),
        "max_delivery_amount": f"{cap_paise // 100}.{cap_paise % 100:02d}",
    }


def _checkout_options(mandate: CustomerAutopayMandate, customer: Customer) -> dict:
    return {
        "setup_id": mandate.setup_id,
        "key": current_app.config["RAZORPAY_KEY_ID"],
        "amount": C.RAZORPAY_AUTOPAY_AUTHORIZATION_AMOUNT_PAISE,
        "currency": "INR",
        "name": "Smart Reorder",
        "description": "Smart Reorder Autopay",
        "order_id": mandate.razorpay_order_id,
        "customer_id": mandate.razorpay_customer_id,
        "recurring": "1",
        "prefill": {
            "name": customer.customer_name or "",
            "contact": re.sub(r"\D", "", customer.phone_e164 or "")[-10:],
        },
        "notes": {"autopay_setup_id": mandate.setup_id},
    }


@bp.get("/autopay")
@login_required
def autopay_status():
    mandate = db_session.get(CustomerAutopayMandate, g.customer_id)
    return jsonify(autopay=_public_status(mandate))


@bp.post("/autopay/disable")
@login_required
def disable_autopay():
    mandate = db_session.get(CustomerAutopayMandate, g.customer_id)
    if (mandate is None or mandate.status != "ACTIVE" or not mandate.razorpay_customer_id
            or not mandate.razorpay_token_id):
        raise Conflict("There is no active Autopay mandate to disable",
                       code="autopay_not_active")

    try:
        result = _client().cancel_token(
            mandate.razorpay_customer_id, mandate.razorpay_token_id)
    except RazorpayError as exc:
        raise GatewayUnavailable("Razorpay could not disable Autopay. Try again shortly.") from exc

    provider_status = result.get("status")
    if provider_status not in {"cancellation_initiated", "cancelled"}:
        log.warning("Razorpay token cancellation returned an unexpected status")
        raise GatewayUnavailable("Razorpay has not confirmed the Autopay cancellation request.")

    mandate.status = "CANCELLED"
    db_session.commit()
    autopay = _public_status(mandate)
    autopay["cancellation_pending"] = provider_status == "cancellation_initiated"
    return jsonify(autopay=autopay)


@bp.post("/autopay/setup")
@login_required
def start_autopay_setup():
    client = _client()
    customer = db_session.get(Customer, g.customer_id)
    if customer is None:
        raise NotFound("Customer not found")
    subscription_service.require_messaging(db_session, customer)

    mandate = db_session.scalars(
        select(CustomerAutopayMandate)
        .where(CustomerAutopayMandate.customer_id == g.customer_id)
        .with_for_update()
    ).one_or_none()
    if mandate and mandate.status == "ACTIVE":
        raise Conflict("Autopay is already set up for this account", code="autopay_already_configured")
    if mandate and mandate.status == "PENDING":
        if mandate.razorpay_order_id:
            return jsonify(checkout=_checkout_options(mandate, customer))
        raise Conflict("Autopay setup is already being prepared. Try again shortly.",
                       code="autopay_setup_in_progress")

    setup_id = uuid.uuid4().hex
    if mandate is None:
        mandate = CustomerAutopayMandate(
            customer_id=g.customer_id,
            status="PENDING",
            setup_id=setup_id,
            max_amount_paise=C.RAZORPAY_AUTOPAY_MAX_AMOUNT_PAISE,
        )
        db_session.add(mandate)
        try:
            with db_session.begin_nested():
                db_session.flush()
        except IntegrityError:
            db_session.rollback()
            raise Conflict("Autopay setup is already being prepared. Try again shortly.",
                           code="autopay_setup_in_progress") from None
    else:
        mandate.status = "PENDING"
        mandate.setup_id = setup_id
        mandate.razorpay_order_id = None
        mandate.razorpay_payment_id = None
        mandate.razorpay_token_id = None
        mandate.mandate_expires_at = None
        mandate.max_amount_paise = C.RAZORPAY_AUTOPAY_MAX_AMOUNT_PAISE
    db_session.commit()

    try:
        provider_customer_id = mandate.razorpay_customer_id
        if not provider_customer_id:
            contact = re.sub(r"\D", "", customer.phone_e164 or "")[-10:]
            provider_customer_id = client.create_customer(customer.customer_name or "Customer", contact)
            mandate = db_session.get(CustomerAutopayMandate, g.customer_id)
            if mandate is None or mandate.setup_id != setup_id:
                raise Conflict("Autopay setup changed. Start setup again.", code="autopay_setup_changed")
            mandate.razorpay_customer_id = provider_customer_id
            db_session.commit()

        expires_at = dt.datetime.now(dt.timezone.utc) + dt.timedelta(
            days=C.RAZORPAY_AUTOPAY_VALIDITY_DAYS)
        order_id = client.create_order(
            customer_id=provider_customer_id,
            setup_id=setup_id,
            max_amount_paise=C.RAZORPAY_AUTOPAY_MAX_AMOUNT_PAISE,
            expire_at=int(expires_at.timestamp()),
            amount_paise=C.RAZORPAY_AUTOPAY_AUTHORIZATION_AMOUNT_PAISE,
        )
        mandate = db_session.get(CustomerAutopayMandate, g.customer_id)
        if mandate is None or mandate.setup_id != setup_id:
            raise Conflict("Autopay setup changed. Start setup again.", code="autopay_setup_changed")
        mandate.razorpay_order_id = order_id
        mandate.mandate_expires_at = expires_at
        db_session.commit()
    except RazorpayError as exc:
        db_session.rollback()
        mandate = db_session.get(CustomerAutopayMandate, g.customer_id)
        if mandate and mandate.setup_id == setup_id:
            mandate.status = "FAILED"
            db_session.commit()
        log.warning("Razorpay test mandate setup failed")
        if isinstance(exc, RazorpayAuthenticationError):
            raise GatewayUnavailable(
                "Razorpay rejected the test credentials. Confirm the key ID and secret are a matching "
                "test-mode pair in .env, then restart Flask.",
            ) from exc
        raise GatewayUnavailable("Razorpay test gateway could not start Autopay setup. Try again.") from exc

    return jsonify(checkout=_checkout_options(mandate, customer)), 201


@bp.post("/autopay/setup/complete")
@login_required
def complete_autopay_setup():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ValidationError("Request body must be a JSON object")
    setup_id = data.get("setup_id")
    order_id = data.get("razorpay_order_id")
    payment_id = data.get("razorpay_payment_id")
    signature = data.get("razorpay_signature")
    if not all(isinstance(value, str) and value for value in (setup_id, order_id, payment_id, signature)):
        raise ValidationError("Payment confirmation is incomplete")
    if (not re.fullmatch(r"[0-9a-f]{32}", setup_id)
            or not re.fullmatch(r"[A-Za-z0-9_]{1,64}", order_id)
            or not re.fullmatch(r"[A-Za-z0-9_]{1,64}", payment_id)
            or not re.fullmatch(r"[0-9a-f]{64}", signature)):
        raise ValidationError("Payment confirmation has an invalid format",
                              code="payment_verification_failed")

    mandate = db_session.get(CustomerAutopayMandate, g.customer_id)
    if mandate is None:
        raise Conflict("Autopay setup was not found", code="autopay_setup_not_found")
    if (mandate.status == "ACTIVE" and mandate.setup_id == setup_id
            and mandate.razorpay_payment_id == payment_id):
        return jsonify(autopay=_public_status(mandate))
    if (mandate.status != "PENDING" or mandate.setup_id != setup_id
            or mandate.razorpay_order_id != order_id):
        raise Conflict("This Autopay setup is no longer pending", code="autopay_setup_not_pending")

    client = _client()
    if not client.verify_checkout_signature(order_id, payment_id, signature):
        raise ValidationError("Razorpay payment signature is invalid", code="payment_verification_failed")

    try:
        payment = client.fetch_payment(payment_id)
        token_id = payment.get("token_id")
        if (payment.get("order_id") != order_id
                or payment.get("customer_id") != mandate.razorpay_customer_id
                or payment.get("amount") != C.RAZORPAY_AUTOPAY_AUTHORIZATION_AMOUNT_PAISE
                or payment.get("currency") != "INR"
                or payment.get("method") != "upi"
                or payment.get("status") != "captured"
                or not isinstance(token_id, str) or not token_id):
            raise Conflict("Razorpay has not confirmed the Autopay authorization yet",
                           code="autopay_authorization_pending")
    except RazorpayError as exc:
        raise GatewayUnavailable("Could not verify the Razorpay authorization. Try again shortly.") from exc

    mandate = db_session.get(CustomerAutopayMandate, g.customer_id)
    if mandate is None or mandate.status != "PENDING" or mandate.setup_id != setup_id:
        raise Conflict("This Autopay setup is no longer pending", code="autopay_setup_not_pending")
    mandate.status = "ACTIVE"
    mandate.razorpay_payment_id = payment_id
    mandate.razorpay_token_id = token_id
    db_session.commit()
    return jsonify(autopay=_public_status(mandate))
