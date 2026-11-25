"""customer_auth tokens and the Flask adapter (app/auth.py). No database."""
import pytest
from flask import Flask, g

from customer_auth import (
    AuthConfigError, AuthSettings, ExpiredTokenError, InvalidTokenError, MissingTokenError, TokenService,
    bearer_token_from_header,
)

SETTINGS = AuthSettings(secret_key="s" * 40, max_age_seconds=60)
TOKENS = TokenService(SETTINGS)


def test_round_trip():
    assert TOKENS.verify(TOKENS.issue("CUST-0001")) == "CUST-0001"
    assert TOKENS.verify(TOKENS.issue("  CUST-0002 ")) == "CUST-0002"


def test_tampered_or_foreign_tokens_are_rejected():
    token = TOKENS.issue("CUST-0001")
    payload, _, signature = token.rpartition(".")
    forged = TokenService(AuthSettings(secret_key="z" * 40)).issue("CUST-0001")
    for bad in [token[:-2] + ("AA" if token[-2:] != "AA" else "BB"), forged, "abc", payload + ".", "a.b.c"]:
        with pytest.raises(InvalidTokenError):
            TOKENS.verify(bad)
    with pytest.raises(MissingTokenError):
        TOKENS.verify("")


def test_expiry(monkeypatch):
    token = TOKENS.issue("CUST-0001")
    import itsdangerous.timed
    real_time = itsdangerous.timed.time.time
    monkeypatch.setattr(itsdangerous.timed.time, "time", lambda: real_time() + 61)
    with pytest.raises(ExpiredTokenError):
        TOKENS.verify(token)


@pytest.mark.parametrize("secret", ["", "short", "change-me-to-a-long-random-string", "CHANGE-ME" + "x" * 40])
def test_weak_or_placeholder_secrets_are_refused(secret):
    with pytest.raises(AuthConfigError):
        AuthSettings(secret_key=secret)


@pytest.mark.parametrize("bad", ["", " ", "x" * 65, None])
def test_issue_rejects_bad_customer_ids(bad):
    with pytest.raises(ValueError):
        TOKENS.issue(bad)


@pytest.mark.parametrize("header,ok", [("Bearer abc", True), ("bearer   abc ", True), ("Basic abc", False),
                                        ("Bearer", False), ("", False), (None, False)])
def test_bearer_header_parsing(header, ok):
    if ok:
        assert bearer_token_from_header(header) == "abc"
    else:
        with pytest.raises(MissingTokenError):
            bearer_token_from_header(header)


def test_settings_from_env(monkeypatch, tmp_path):
    from database import connection
    monkeypatch.setattr(connection, "ENV_FILE", tmp_path / "none.env")
    monkeypatch.setenv("AUTH_SECRET_KEY", "k" * 40)
    monkeypatch.setenv("AUTH_TOKEN_MAX_AGE_SECONDS", "120")
    monkeypatch.setenv("AUTH_DEMO_LOGIN", "true")
    s = AuthSettings.from_env()
    assert (s.max_age_seconds, s.demo_login_enabled) == (120, True)
    monkeypatch.delenv("AUTH_DEMO_LOGIN")
    assert AuthSettings.from_env().demo_login_enabled is False  # off unless explicitly enabled
    monkeypatch.delenv("AUTH_SECRET_KEY")
    with pytest.raises(AuthConfigError):
        AuthSettings.from_env()


# ------------------------------------------------------------------ Flask adapter
@pytest.fixture
def flask_client():
    from app.auth import EXTENSION_KEY, login_required
    app = Flask("test")
    app.extensions[EXTENSION_KEY] = TOKENS

    @app.get("/private")
    @login_required
    def private():
        return {"customer_id": g.customer_id}

    return app.test_client()


def test_flask_login_required(flask_client):
    ok = flask_client.get("/private", headers={"Authorization": f"Bearer {TOKENS.issue('CUST-0007')}"})
    assert ok.status_code == 200 and ok.get_json() == {"customer_id": "CUST-0007"}
    for headers in ({}, {"Authorization": "Bearer junk"}):
        r = flask_client.get("/private", headers=headers)
        assert r.status_code == 401 and r.headers["WWW-Authenticate"] == "Bearer"
        assert r.get_json()["error"]["code"] == "unauthorized"


def test_tokens_from_the_api_work_in_flask(flask_client):
    """Both apps share AUTH_SECRET_KEY, so a token issued by FastAPI is valid in Flask."""
    from fastapi.testclient import TestClient

    from recommendation_api.factory import create_app
    from recommendation_api.service import RecommendationService
    from recommendation_api.settings import ApiSettings
    from recommendation_engine import RecommendationEngine
    from tests.recommendation_fixtures import product, purchases, repo
    api = create_app(ApiSettings(auth=AuthSettings(secret_key="s" * 40, demo_login_enabled=True), log_level="WARNING"),
                     service=RecommendationService(RecommendationEngine(
                         repository=repo(purchases("CUST-0007", "A", [3]), [product("A")], customers=("CUST-0007",)))))
    token = TestClient(api).post("/api/v1/auth/demo-login", json={"customer_id": "CUST-0007"}).json()["access_token"]
    r = flask_client.get("/private", headers={"Authorization": f"Bearer {token}"})
    assert r.get_json() == {"customer_id": "CUST-0007"}
