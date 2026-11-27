"""Razorpay test-mode client for UPI Autopay mandate enrollment."""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
from typing import Any
from urllib.parse import quote

import requests

log = logging.getLogger(__name__)
API_BASE = "https://api.razorpay.com/v1"


class RazorpayError(RuntimeError):
    pass


class RazorpayAuthenticationError(RazorpayError):
    pass


class RazorpayClient:
    def __init__(self, key_id: str, key_secret: str, timeout: float = 10.0):
        if not key_id.startswith("rzp_test_") or len(key_id) == len("rzp_test_"):
            raise ValueError("Only Razorpay test-mode keys are supported")
        if not key_secret:
            raise ValueError("Razorpay test secret is not configured")
        self.key_id = key_id
        self._http = requests.Session()
        self._http.auth = (key_id, key_secret)
        self._http.headers.update({"Accept": "application/json"})
        self._timeout = timeout
        self._key_secret = key_secret

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            response = self._http.request(
                method, f"{API_BASE}{path}", timeout=self._timeout, **kwargs)
        except requests.RequestException as exc:
            log.warning("Razorpay test API request failed for %s %s (%s)",
                        method, path, type(exc).__name__)
            raise RazorpayError("Razorpay test gateway is unreachable") from exc

        try:
            data = response.json()
        except ValueError as exc:
            log.warning("Razorpay test API returned a non-JSON response for %s %s (HTTP %s)",
                        method, path, response.status_code)
            raise RazorpayError("Razorpay test gateway returned an invalid response") from exc
        if response.status_code == 401:
            log.warning("Razorpay test API rejected credentials for %s %s", method, path)
            raise RazorpayAuthenticationError(
                "Razorpay rejected the test credentials. Check that the key ID and secret are a matching test-mode pair.")
        if not response.ok or not isinstance(data, dict):
            error = data.get("error") if isinstance(data, dict) else None
            code = error.get("code") if isinstance(error, dict) else None
            field = error.get("field") if isinstance(error, dict) else None
            code = code if isinstance(code, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", code) else "unknown"
            field = field if isinstance(field, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", field) else "unknown"
            log.warning("Razorpay test API returned HTTP %s for %s %s (code=%s field=%s)",
                        response.status_code, method, path, code, field)
            raise RazorpayError("Razorpay test gateway could not complete the request")
        return data

    def create_customer(self, name: str, contact: str) -> str:
        customer = self._request("POST", "/customers", params={"fail_existing": 0}, json={
            "name": name,
            "contact": contact,
        })
        customer_id = customer.get("id")
        if not isinstance(customer_id, str) or not customer_id:
            raise RazorpayError("Razorpay did not return a customer ID")
        return customer_id

    def create_order(
        self,
        *,
        customer_id: str,
        setup_id: str,
        max_amount_paise: int,
        expire_at: int,
        amount_paise: int,
    ) -> str:
        order = self._request("POST", "/orders", json={
            "amount": amount_paise,
            "currency": "INR",
            "receipt": f"ap-{setup_id}",
            "customer_id": customer_id,
            "method": "upi",
            "notes": {"autopay_setup_id": setup_id},
            "description": "Smart Reorder Autopay",
            "token": {
                "max_amount": max_amount_paise,
                "expire_at": expire_at,
                "frequency": "as_presented",
            },
        })
        order_id = order.get("id")
        if not isinstance(order_id, str) or not order_id:
            raise RazorpayError("Razorpay did not return an order ID")
        return order_id

    def fetch_payment(self, payment_id: str) -> dict[str, Any]:
        return self._request("GET", f"/payments/{payment_id}")

    def cancel_token(self, customer_id: str, token_id: str) -> dict[str, Any]:
        safe_customer_id = quote(customer_id, safe="")
        safe_token_id = quote(token_id, safe="")
        return self._request("PUT", f"/customers/{safe_customer_id}/tokens/{safe_token_id}/cancel")

    def verify_checkout_signature(self, order_id: str, payment_id: str, signature: str) -> bool:
        message = f"{order_id}|{payment_id}".encode()
        expected = hmac.new(self._key_secret.encode(), message, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)
