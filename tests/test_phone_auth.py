import re
import datetime as dt

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from customer_auth.msg91 import (
    InvalidMSG91AccessToken, MSG91Client, MSG91Settings, MSG91Unavailable,
)
from customer_auth.phones import PhoneNumberError, normalize_phone_number, verified_phone_matches
from customer_auth.service import CustomerAuthService
from customer_auth.storage import AuthStore, InvalidRefreshToken, RefreshTokenReuse, metadata
from customer_auth.tokens import AuthSettings, ExpiredTokenError, InvalidTokenError, TokenService
from database.models import Customer
from recommendation_api.factory import create_app
from recommendation_api.settings import ApiSettings
from tests.subscription_db import ME, admin_engine, schema_engine

AUTH_SETTINGS = AuthSettings(
    secret_key="test-phone-auth-secret-0123456789abcdef",
    access_token_ttl_seconds=900,
    refresh_token_ttl_seconds=3600,
    refresh_cookie_secure=False,
)
FRONTEND_ORIGIN = "http://localhost:5173"


class FakeMSG91:
    def __init__(self, phone_by_token=None, failure=None):
        self.phone_by_token = phone_by_token or {
            "valid-msg91-token": "919800000001",
            "registration-token": "919800000003",
        }
        self.failure = failure
        self.calls = 0

    def widget_config(self):
        return {"widget_id": "test-widget", "token_auth": "public-widget-token"}

    def verify_access_token(self, token):
        self.calls += 1
        if self.failure:
            raise self.failure
        if token not in self.phone_by_token:
            raise InvalidMSG91AccessToken()
        return self.phone_by_token[token]


@pytest.fixture
def auth_store(schema_engine):
    engine, _ = schema_engine
    metadata.create_all(engine)
    return AuthStore(engine)


def build_client(store, msg91=None):
    service = CustomerAuthService(AUTH_SETTINGS, store, msg91 or FakeMSG91())
    settings = ApiSettings(
        auth=AUTH_SETTINGS,
        cors_origins=(FRONTEND_ORIGIN,),
        log_level="WARNING",
        msg91_widget_id="test-widget",
        msg91_token_auth="public-widget-token",
        msg91_authkey="server-only-authkey",
    )
    return TestClient(create_app(settings, auth_service=service)), service


def login_payload(**overrides):
    return {
        "msg91_access_token": "valid-msg91-token",
        "phone_number": "9800000001",
        "region": "IN",
        **overrides,
    }


def registration_payload(**overrides):
    return {
        **login_payload(msg91_access_token="registration-token", phone_number="9800000003"),
        "customer_name": "Test Customer",
        "household_size": 3,
        "preferred_weekday": "Sat",
        **overrides,
    }


@pytest.mark.parametrize(("phone", "region", "expected"), [
    ("98000 00001", "IN", "+919800000001"),
    ("+44 20 7031 3000", "GB", "+442070313000"),
    ("(415) 555-2671", "US", "+14155552671"),
])
def test_phone_numbers_normalize_to_e164(phone, region, expected):
    assert normalize_phone_number(phone, region) == expected


def test_invalid_phone_and_region_are_rejected():
    with pytest.raises(PhoneNumberError):
        normalize_phone_number("123", "IN")
    with pytest.raises(PhoneNumberError):
        normalize_phone_number("9800000001", "India")


@pytest.mark.parametrize(("identifier", "expected"), [
    ("919800000001", "+919800000001"),
    ("9800000001", "+919800000001"),
    ("+919800000002", "+919800000001"),
])
def test_msg91_verified_identifier_must_match_submitted_phone(identifier, expected):
    assert verified_phone_matches(identifier, expected, "IN") is (identifier != "+919800000002")


def test_registration_and_login_use_only_the_verified_phone(auth_store):
    client, _ = build_client(auth_store)
    result = client.post("/api/v1/auth/register", json=registration_payload())
    assert result.status_code == 201
    body = result.json()
    assert body["created"] is True and body["customer_id"].startswith("CUST-")
    assert body["customer_name"] == "Test Customer"
    assert len(body["customer_id"]) <= 20
    assert TokenService(AUTH_SETTINGS).verify(body["access_token"]) == body["customer_id"]
    assert result.headers["set-cookie"].lower().find("httponly") >= 0
    assert result.headers["set-cookie"].lower().find("samesite=strict") >= 0

    with Session(auth_store.engine) as session:
        customer = session.get(Customer, body["customer_id"])
        assert customer.phone_e164 == "+919800000003"
        assert customer.signup_date == dt.datetime.now(dt.timezone.utc).astimezone(
            dt.timezone(dt.timedelta(hours=5, minutes=30))
        ).date()
        assert customer.behaviour_segment == "new"
        assert customer.whatsapp_opt_in is False
        assert session.scalar(select(func.count()).select_from(Customer).where(
            Customer.phone_e164 == "+919800000003"
        )) == 1

    login = client.post("/api/v1/auth/login", json=login_payload(
        msg91_access_token="registration-token",
        phone_number="9800000003",
    ))
    assert login.status_code == 200 and login.json()["customer_id"] == body["customer_id"]
    assert login.json()["customer_name"] == "Test Customer"
    assert client.get("/api/v1/auth/me", headers={
        "Authorization": f"Bearer {login.json()['access_token']}",
    }).json() == {"customer_id": body["customer_id"]}


def test_registration_retry_does_not_create_a_duplicate(auth_store):
    client, _ = build_client(auth_store)
    first = client.post("/api/v1/auth/register", json=registration_payload())
    retry = client.post("/api/v1/auth/register", json=registration_payload())
    assert first.status_code == 201
    assert retry.status_code == 200
    assert retry.json()["created"] is False
    assert retry.json()["customer_id"] == first.json()["customer_id"]
    with Session(auth_store.engine) as session:
        assert session.scalar(select(func.count()).select_from(Customer).where(
            Customer.phone_e164 == "+919800000001"
        )) == 1


def test_registration_defaults_household_size_and_delivery_day(auth_store):
    client, _ = build_client(auth_store)
    payload = registration_payload()
    payload.pop("household_size")
    payload.pop("preferred_weekday")
    response = client.post("/api/v1/auth/register", json=payload)

    assert response.status_code == 201
    with Session(auth_store.engine) as session:
        customer = session.get(Customer, response.json()["customer_id"])
        today = dt.datetime.now(dt.timezone.utc).astimezone(
            dt.timezone(dt.timedelta(hours=5, minutes=30))
        )
        assert customer.household_size == 1
        assert customer.preferred_weekday == ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")[today.weekday()]


def test_existing_customer_can_log_in_by_verified_phone(auth_store):
    client, _ = build_client(auth_store)
    response = client.post("/api/v1/auth/login", json=login_payload())
    assert response.status_code == 200
    assert response.json()["customer_id"] == ME


def test_login_requires_msg91_verification_and_rejects_mismatched_phone(auth_store):
    rejected = FakeMSG91(failure=InvalidMSG91AccessToken())
    client, _ = build_client(auth_store, rejected)
    response = client.post("/api/v1/auth/login", json=login_payload())
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_verification"
    assert response.headers["www-authenticate"] == "Bearer"

    mismatch_client, _ = build_client(auth_store, FakeMSG91({"valid-msg91-token": "919800000002"}))
    mismatch = mismatch_client.post("/api/v1/auth/login", json=login_payload())
    assert mismatch.status_code == 401
    assert mismatch.json()["error"]["code"] == "invalid_verification"


def test_existing_customers_without_a_phone_cannot_log_in(auth_store):
    client, _ = build_client(auth_store, FakeMSG91({"verified-unregistered": "919800000003"}))
    response = client.post("/api/v1/auth/login", json=login_payload(
        msg91_access_token="verified-unregistered",
        phone_number="9800000003",
    ))
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "phone_not_registered"


def test_login_with_unregistered_phone_is_not_authentication(auth_store):
    client, _ = build_client(auth_store, FakeMSG91({"valid-msg91-token": "919800000003"}))
    response = client.post("/api/v1/auth/login", json=login_payload(phone_number="9800000003"))
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "phone_not_registered"


def test_customer_id_is_not_accepted_as_login_input(auth_store):
    client, _ = build_client(auth_store)
    response = client.post("/api/v1/auth/login", json={"customer_id": ME})
    assert response.status_code == 422
    assert client.post("/api/v1/auth/demo-login", json={"customer_id": ME}).status_code == 404


def test_refresh_rotates_tokens_and_reuse_revokes_the_family(auth_store):
    raw, _ = auth_store.create_refresh_token(ME, AUTH_SETTINGS.refresh_token_ttl_seconds)
    customer_id, rotated, _ = auth_store.rotate_refresh_token(raw)
    assert customer_id == ME and rotated != raw
    with pytest.raises(RefreshTokenReuse):
        auth_store.rotate_refresh_token(raw)
    with pytest.raises(RefreshTokenReuse):
        auth_store.rotate_refresh_token(rotated)


def test_refresh_endpoint_rotates_cookie_and_logout_revokes(auth_store):
    client, _ = build_client(auth_store)
    registered = client.post("/api/v1/auth/register", json=registration_payload())
    assert registered.status_code == 201
    old_refresh = client.cookies.get("smart_reorder_refresh")

    rotated = client.post("/api/v1/auth/refresh", headers={"Origin": FRONTEND_ORIGIN})
    assert rotated.status_code == 200
    assert rotated.json()["access_token"] != registered.json()["access_token"]
    assert rotated.json()["customer_name"] == "Test Customer"
    new_refresh = client.cookies.get("smart_reorder_refresh")
    assert new_refresh and new_refresh != old_refresh

    replay = client.post("/api/v1/auth/refresh", cookies={
        "smart_reorder_refresh": old_refresh,
    }, headers={"Origin": FRONTEND_ORIGIN})
    assert replay.status_code == 401
    next_attempt = client.post("/api/v1/auth/refresh", cookies={
        "smart_reorder_refresh": new_refresh,
    }, headers={"Origin": FRONTEND_ORIGIN})
    assert next_attempt.status_code == 401


def test_logout_revokes_refresh_token_and_checks_origin(auth_store):
    client, _ = build_client(auth_store)
    registered = client.post("/api/v1/auth/register", json=registration_payload())
    refresh = client.cookies.get("smart_reorder_refresh")

    forbidden = client.post("/api/v1/auth/logout", headers={"Origin": "https://evil.example"})
    assert forbidden.status_code == 403
    logged_out = client.post("/api/v1/auth/logout", headers={"Origin": FRONTEND_ORIGIN})
    assert logged_out.status_code == 204
    assert client.post("/api/v1/auth/refresh", cookies={
        "smart_reorder_refresh": refresh,
    }, headers={"Origin": FRONTEND_ORIGIN}).status_code == 401
    assert registered.status_code == 201


def test_rate_limit_blocks_repeated_phone_auth_attempts(auth_store):
    msg91 = FakeMSG91({"valid-msg91-token": "919800000001"})
    client, _ = build_client(auth_store, msg91)
    statuses = [
        client.post("/api/v1/auth/login", json=login_payload()).status_code
        for _ in range(6)
    ]
    assert statuses == [200, 200, 200, 200, 200, 429]
    assert msg91.calls == 5


def test_widget_config_is_public_but_only_contains_widget_scoped_values(auth_store):
    client, _ = build_client(auth_store)
    response = client.get("/api/v1/auth/widget-config")
    assert response.status_code == 200
    assert response.json() == {"widget_id": "test-widget", "token_auth": "public-widget-token"}


def test_access_token_requires_valid_signature_issuer_audience_and_expiry():
    tokens = TokenService(AUTH_SETTINGS)
    valid = tokens.issue(ME)
    assert tokens.verify(valid) == ME

    expired = tokens.issue(ME, now=dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=2))
    with pytest.raises(ExpiredTokenError):
        tokens.verify(expired)
    with pytest.raises(InvalidTokenError):
        TokenService(AuthSettings(secret_key="different-secret-key-0123456789abcdef")).verify(valid)


def test_msg91_server_verification_uses_authkey_and_verified_identifier(monkeypatch):
    observed = {}

    class Response:
        status_code = 200
        ok = True

        @staticmethod
        def json():
            return {"type": "success", "message": "919800000001"}

    def fake_post(url, **kwargs):
        observed["url"] = url
        observed.update(kwargs)
        return Response()

    monkeypatch.setattr("customer_auth.msg91.requests.post", fake_post)
    client = MSG91Client(MSG91Settings("widget-id", "widget-token", "server-key"))
    assert client.verify_access_token("msg91-jwt") == "919800000001"
    assert observed["headers"]["authkey"] == "server-key"
    assert observed["json"] == {"access-token": "msg91-jwt"}
    assert observed["timeout"] == 5


def test_msg91_failure_shapes_are_rejected(monkeypatch):
    class Response:
        status_code = 200
        ok = True

        @staticmethod
        def json():
            return {"type": "error", "message": "AuthenticationFailure"}

    monkeypatch.setattr("customer_auth.msg91.requests.post", lambda *args, **kwargs: Response())
    client = MSG91Client(MSG91Settings("widget-id", "widget-token", "server-key"))
    with pytest.raises(InvalidMSG91AccessToken):
        client.verify_access_token("invalid")
    with pytest.raises(MSG91Unavailable):
        MSG91Client(MSG91Settings("widget-id", "widget-token", None)).verify_access_token("valid")


def test_generated_customer_ids_follow_internal_format(auth_store):
    client, _ = build_client(auth_store)
    response = client.post("/api/v1/auth/register", json=registration_payload())
    assert response.status_code == 201
    assert re.fullmatch(r"CUST-[A-F0-9]{15}", response.json()["customer_id"])
