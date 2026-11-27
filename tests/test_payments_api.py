import hashlib
import hmac

import pytest

from app import create_app
from app.payments import routes
from subscription_db import ME, OTHER, admin_engine, bearer, schema_engine, test_env  # noqa: F401

KEY_ID = "rzp_test_example"
KEY_SECRET = "test_gateway_secret"


class FakeRazorpayClient:
    def __init__(self):
        self.customer_calls = 0
        self.order_calls = 0
        self.cancel_calls = []
        self.payment_status = "captured"

    def create_customer(self, name, contact):
        self.customer_calls += 1
        assert name
        assert contact == "9800000001"
        return "cust_test_123"

    def create_order(self, **kwargs):
        self.order_calls += 1
        assert kwargs["customer_id"] == "cust_test_123"
        assert kwargs["max_amount_paise"] == 500_000
        assert kwargs["amount_paise"] == 100
        return "order_test_123"

    def verify_checkout_signature(self, order_id, payment_id, signature):
        expected = hmac.new(
            KEY_SECRET.encode(), f"{order_id}|{payment_id}".encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)

    def fetch_payment(self, payment_id):
        assert payment_id == "pay_test_123"
        return {
            "id": payment_id,
            "order_id": "order_test_123",
            "customer_id": "cust_test_123",
            "amount": 100,
            "currency": "INR",
            "method": "upi",
            "status": self.payment_status,
            "token_id": "token_test_123",
        }

    def cancel_token(self, customer_id, token_id):
        self.cancel_calls.append((customer_id, token_id))
        return {"status": "cancellation_initiated"}


@pytest.fixture
def client(schema_engine, monkeypatch):
    app = create_app("test", overrides={
        "DB_SEARCH_PATH": schema_engine[1],
        "RAZORPAY_KEY_ID": KEY_ID,
        "RAZORPAY_KEY_SECRET": KEY_SECRET,
    })
    fake_gateway = FakeRazorpayClient()
    monkeypatch.setattr(routes, "_client", lambda: fake_gateway)
    yield app.test_client(), fake_gateway
    app.extensions["db_engine"].dispose()


def test_autopay_setup_is_idempotent_and_creates_customer_level_mandate(client):
    http, gateway = client
    started = http.post("/api/payments/autopay/setup", headers=bearer(ME))
    assert started.status_code == 201
    checkout = started.json["checkout"]
    assert checkout["amount"] == 100
    assert checkout["currency"] == "INR"
    assert checkout["recurring"] == "1"
    assert checkout["customer_id"] == "cust_test_123"

    repeated = http.post("/api/payments/autopay/setup", headers=bearer(ME))
    assert repeated.status_code == 200
    assert repeated.json["checkout"]["order_id"] == checkout["order_id"]
    assert gateway.customer_calls == 1
    assert gateway.order_calls == 1


def test_only_verified_captured_upi_authorization_activates_mandate(client):
    http, _ = client
    started = http.post("/api/payments/autopay/setup", headers=bearer(ME))
    checkout = started.json["checkout"]
    payment_id = "pay_test_123"
    signature = hmac.new(
        KEY_SECRET.encode(),
        f"{checkout['order_id']}|{payment_id}".encode(),
        hashlib.sha256,
    ).hexdigest()

    completed = http.post("/api/payments/autopay/setup/complete", headers=bearer(ME), json={
        "setup_id": checkout["setup_id"],
        "razorpay_order_id": checkout["order_id"],
        "razorpay_payment_id": payment_id,
        "razorpay_signature": signature,
    })
    assert completed.status_code == 200
    assert completed.json["autopay"]["status"] == "active"

    repeated = http.post("/api/payments/autopay/setup/complete", headers=bearer(ME), json={
        "setup_id": checkout["setup_id"],
        "razorpay_order_id": checkout["order_id"],
        "razorpay_payment_id": payment_id,
        "razorpay_signature": signature,
    })
    assert repeated.status_code == 200

    status = http.get("/api/payments/autopay", headers=bearer(ME))
    assert status.json["autopay"]["status"] == "active"


def test_invalid_signature_does_not_activate_mandate(client):
    http, _ = client
    checkout = http.post("/api/payments/autopay/setup", headers=bearer(ME)).json["checkout"]
    response = http.post("/api/payments/autopay/setup/complete", headers=bearer(ME), json={
        "setup_id": checkout["setup_id"],
        "razorpay_order_id": checkout["order_id"],
        "razorpay_payment_id": "pay_test_123",
        "razorpay_signature": "invalid",
    })
    assert response.status_code == 400
    assert response.json["error"]["code"] == "payment_verification_failed"
    assert http.get("/api/payments/autopay", headers=bearer(ME)).json["autopay"]["status"] == "pending"


def test_uncaptured_authorization_does_not_activate_mandate(client):
    http, gateway = client
    checkout = http.post("/api/payments/autopay/setup", headers=bearer(ME)).json["checkout"]
    gateway.payment_status = "authorized"
    payment_id = "pay_test_123"
    signature = hmac.new(
        KEY_SECRET.encode(),
        f"{checkout['order_id']}|{payment_id}".encode(),
        hashlib.sha256,
    ).hexdigest()

    response = http.post("/api/payments/autopay/setup/complete", headers=bearer(ME), json={
        "setup_id": checkout["setup_id"],
        "razorpay_order_id": checkout["order_id"],
        "razorpay_payment_id": payment_id,
        "razorpay_signature": signature,
    })
    assert response.status_code == 409
    assert response.json["error"]["code"] == "autopay_authorization_pending"
    assert http.get("/api/payments/autopay", headers=bearer(ME)).json["autopay"]["status"] == "pending"


def test_mandate_status_is_scoped_to_authenticated_customer(client):
    http, _ = client
    http.post("/api/payments/autopay/setup", headers=bearer(ME))
    other_status = http.get("/api/payments/autopay", headers=bearer(OTHER))
    assert other_status.status_code == 200
    assert other_status.json["autopay"]["status"] == "not_setup"


def _activate_autopay(http):
    checkout = http.post("/api/payments/autopay/setup", headers=bearer(ME)).json["checkout"]
    payment_id = "pay_test_123"
    signature = hmac.new(
        KEY_SECRET.encode(),
        f"{checkout['order_id']}|{payment_id}".encode(),
        hashlib.sha256,
    ).hexdigest()
    response = http.post("/api/payments/autopay/setup/complete", headers=bearer(ME), json={
        "setup_id": checkout["setup_id"],
        "razorpay_order_id": checkout["order_id"],
        "razorpay_payment_id": payment_id,
        "razorpay_signature": signature,
    })
    assert response.status_code == 200


def test_disable_autopay_cancels_provider_token_and_allows_setup_again(client):
    http, gateway = client
    _activate_autopay(http)

    disabled = http.post("/api/payments/autopay/disable", headers=bearer(ME), json={})

    assert disabled.status_code == 200
    assert disabled.json["autopay"]["status"] == "cancelled"
    assert disabled.json["autopay"]["setup_later_allowed"] is True
    assert disabled.json["autopay"]["cancellation_pending"] is True
    assert gateway.cancel_calls == [("cust_test_123", "token_test_123")]
    assert http.post("/api/payments/autopay/setup", headers=bearer(ME)).status_code == 201


def test_subscription_mode_must_match_autopay_status(client):
    http, _ = client

    missing_mandate = http.post("/api/subscriptions", headers=bearer(ME), json={
        "product_id": "bb-242671",
        "autopay_mode": "existing",
    })
    assert missing_mandate.status_code == 409
    assert missing_mandate.json["error"]["code"] == "autopay_not_configured"

    _activate_autopay(http)
    active_mandate = http.post("/api/subscriptions", headers=bearer(ME), json={
        "product_id": "bb-242671",
        "autopay_mode": "later",
    })
    assert active_mandate.status_code == 409
    assert active_mandate.json["error"]["code"] == "autopay_already_configured"


def test_setup_requires_authentication(client):
    response = client[0].post("/api/payments/autopay/setup")
    assert response.status_code == 401
