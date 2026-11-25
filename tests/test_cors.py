"""CORS for the React frontend calling Flask from the browser."""
import pytest

from app import create_app
from subscription_db import ME, bearer, test_env, admin_engine, schema_engine  # noqa: F401

FRONTEND = "http://localhost:5173"


@pytest.fixture
def client(schema_engine):
    app = create_app("test", overrides={"DB_SEARCH_PATH": schema_engine[1]})
    yield app.test_client()
    app.extensions["db_engine"].dispose()


def test_preflight_allows_frontend_without_auth(client):
    r = client.options("/api/subscriptions", headers={
        "Origin": FRONTEND, "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "authorization, content-type"})
    assert r.status_code == 200                       # answered before @login_required
    assert r.headers["Access-Control-Allow-Origin"] == FRONTEND
    assert "PATCH" in r.headers["Access-Control-Allow-Methods"]
    assert "Authorization" in r.headers["Access-Control-Allow-Headers"]
    assert "Access-Control-Allow-Credentials" not in r.headers


def test_actual_responses_carry_cors_headers_including_errors(client):
    ok = client.get("/api/subscriptions", headers={"Origin": FRONTEND, **bearer(ME)})
    assert ok.status_code == 200 and ok.headers["Access-Control-Allow-Origin"] == FRONTEND
    denied = client.get("/api/subscriptions", headers={"Origin": FRONTEND})
    assert denied.status_code == 401 and denied.headers["Access-Control-Allow-Origin"] == FRONTEND
    created = client.post("/api/subscriptions", json={"product_id": "bb-milk"}, headers={"Origin": FRONTEND, **bearer(ME)})
    assert created.status_code == 201 and created.headers["Access-Control-Expose-Headers"] == "Location"


def test_other_origins_get_no_cors_headers(client):
    r = client.get("/api/subscriptions", headers={"Origin": "https://evil.example", **bearer(ME)})
    assert "Access-Control-Allow-Origin" not in r.headers and "Origin" in r.headers["Vary"]
