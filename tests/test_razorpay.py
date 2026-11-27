import hashlib
import hmac
import logging

import pytest
from flask import Flask

from app.payments.razorpay import RazorpayAuthenticationError, RazorpayClient, RazorpayError
from app.payments.routes import GatewayUnavailable, _client


def test_client_refuses_live_keys():
    with pytest.raises(ValueError, match="test-mode"):
        RazorpayClient("rzp_live_example", "secret")


def test_flask_gateway_configuration_rejects_live_keys():
    app = Flask(__name__)
    app.config.update(RAZORPAY_KEY_ID="rzp_live_example", RAZORPAY_KEY_SECRET="secret")

    with app.app_context(), pytest.raises(GatewayUnavailable):
        _client()


def test_create_order_uses_flexible_upi_mandate(monkeypatch):
    client = RazorpayClient("rzp_test_example", "secret")
    calls = []

    def request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        return {"id": "order_test_123"}

    monkeypatch.setattr(client, "_request", request)
    order_id = client.create_order(
        customer_id="cust_test_123",
        setup_id="a" * 32,
        max_amount_paise=500_000,
        expire_at=1_900_000_000,
        amount_paise=100,
    )

    assert order_id == "order_test_123"
    method, path, kwargs = calls[0]
    assert method == "POST" and path == "/orders"
    assert kwargs["json"]["amount"] == 100
    assert kwargs["json"]["currency"] == "INR"
    assert kwargs["json"]["method"] == "upi"
    assert kwargs["json"]["token"] == {
        "max_amount": 500_000,
        "expire_at": 1_900_000_000,
        "frequency": "as_presented",
    }


def test_checkout_signature_is_verified():
    client = RazorpayClient("rzp_test_example", "secret")
    signature = hmac.new(
        b"secret", b"order_test_123|pay_test_456", hashlib.sha256).hexdigest()

    assert client.verify_checkout_signature("order_test_123", "pay_test_456", signature)
    assert not client.verify_checkout_signature("order_test_123", "pay_test_456", "bad")


def test_unauthorized_gateway_response_identifies_test_credentials(monkeypatch):
    class Response:
        status_code = 401
        ok = False

        @staticmethod
        def json():
            return {"error": {"description": "Authentication failed"}}

    client = RazorpayClient("rzp_test_example", "secret")
    monkeypatch.setattr(client._http, "request", lambda *args, **kwargs: Response())

    with pytest.raises(RazorpayAuthenticationError, match="matching test-mode pair"):
        client._request("POST", "/customers", json={"name": "Customer"})


def test_gateway_error_logs_only_safe_metadata(monkeypatch, caplog):
    class Response:
        status_code = 400
        ok = False

        @staticmethod
        def json():
            return {
                "error": {
                    "code": "BAD_REQUEST_ERROR",
                    "field": "contact",
                    "description": "Sensitive customer details",
                },
            }

    client = RazorpayClient("rzp_test_example", "secret")
    monkeypatch.setattr(client._http, "request", lambda *args, **kwargs: Response())

    with caplog.at_level(logging.WARNING), pytest.raises(RazorpayError):
        client._request("POST", "/customers", json={"name": "Customer"})

    assert "HTTP 400 for POST /customers" in caplog.text
    assert "code=BAD_REQUEST_ERROR field=contact" in caplog.text
    assert "Sensitive customer details" not in caplog.text


def test_cancel_token_uses_customer_scoped_razorpay_endpoint(monkeypatch):
    client = RazorpayClient("rzp_test_example", "secret")
    calls = []

    def request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        return {"status": "cancellation_initiated"}

    monkeypatch.setattr(client, "_request", request)

    assert client.cancel_token("cust_test_123", "token_test_123") == {
        "status": "cancellation_initiated",
    }
    assert calls == [("PUT", "/customers/cust_test_123/tokens/token_test_123/cancel", {})]
