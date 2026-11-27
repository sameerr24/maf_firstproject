"""The admin app itself: separate from the customer app, no auth imported, no scheduler, and
the localhost / JSON guard that stands in for a login. Throwaway schema on the test database."""
import subprocess
import sys
from pathlib import Path

import pytest

from app import create_app
from app.admin import create_admin_app
from app.admin.config import AdminConfig
from subscription_db import test_env, admin_engine, schema_engine  # noqa: F401

ROOT = Path(__file__).resolve().parent.parent
BASE = "http://localhost:5002"


@pytest.fixture
def admin_app(schema_engine):
    app = create_admin_app("test", overrides={"DB_SEARCH_PATH": schema_engine[1], "DEV_ENDPOINTS": True})
    yield app
    app.extensions["db_engine"].dispose()


@pytest.fixture
def client(admin_app):
    return admin_app.test_client()


def test_health_on_localhost_and_127(client):
    for host in ("localhost:5002", "127.0.0.1:5002", "LOCALHOST:5002"):
        r = client.get("/api/admin/health", base_url=f"http://{host}")
        assert r.status_code == 200 and r.json == {"status": "ok"}, host


@pytest.mark.parametrize("host", ["evil.example:5002", "localhost", "localhost:5001", "127.0.0.1:80",
                                  "192.168.1.20:5002", "localhost.evil.example:5002"])
def test_other_hosts_are_refused(client, host):
    r = client.get("/api/admin/health", base_url=f"http://{host}")
    assert r.status_code == 403 and r.json["error"]["code"] == "forbidden"


def test_writes_need_a_json_body(client):
    url = "/api/dev/run-scheduler?now=2026-11-27T09:00"
    form = client.post(url, base_url=BASE, data={"x": "1"})
    assert form.status_code == 415 and form.json["error"]["code"] == "unsupported_media_type"
    assert client.post(url, base_url=BASE).status_code == 415                     # no body at all
    assert client.post(url, base_url=BASE, data="{}", content_type="text/plain").status_code == 415
    ok = client.post(url, base_url=BASE, json={})
    assert ok.status_code == 200 and ok.json["status"] == "ok"


def test_reads_dont_need_json(client):
    assert client.get("/api/admin/health", base_url=BASE).status_code == 200


def test_cors_allows_only_the_admin_frontend(client):
    for origin in ("http://localhost:5174", "http://127.0.0.1:5174"):
        r = client.get("/api/admin/health", base_url=BASE, headers={"Origin": origin})
        assert r.headers.get("Access-Control-Allow-Origin") == origin
    r = client.get("/api/admin/health", base_url=BASE, headers={"Origin": "http://localhost:5173"})
    assert "Access-Control-Allow-Origin" not in r.headers                         # customer frontend


def test_preflight_for_a_json_post(client):
    r = client.options("/api/dev/run-scheduler", base_url=BASE, headers={
        "Origin": "http://127.0.0.1:5174", "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "Content-Type"})
    assert r.status_code == 200 and r.headers["Access-Control-Allow-Origin"] == "http://127.0.0.1:5174"
    assert "Content-Type" in r.headers["Access-Control-Allow-Headers"]


def test_only_admin_routes_no_customer_ones(admin_app, client):
    rules = {r.rule for r in admin_app.url_map.iter_rules()}
    assert not any(r.startswith(("/api/subscriptions", "/api/orders", "/api/products", "/webhooks")) for r in rules)
    assert client.get("/api/subscriptions", base_url=BASE).json["error"]["code"] == "not_found"   # JSON 404


def test_no_scheduler_in_the_admin_app(admin_app):
    assert admin_app.config["SCHEDULER_ENABLED"] is False and "scheduler" not in admin_app.extensions


def test_admin_config_ignores_scheduler_env_and_reads_admin_keys(monkeypatch):
    monkeypatch.setenv("SCHEDULER_ENABLED", "1")
    monkeypatch.setenv("ADMIN_PORT", "6010")
    monkeypatch.setenv("ADMIN_CORS_ORIGINS", "http://127.0.0.1:6011/")
    cfg = AdminConfig()
    assert cfg.SCHEDULER_ENABLED is False and cfg.ADMIN_PORT == 6010
    assert cfg.CORS_ORIGINS == ("http://127.0.0.1:6011",)


def test_dev_endpoints_only_when_enabled(schema_engine):
    app = create_admin_app("test", overrides={"DB_SEARCH_PATH": schema_engine[1]})
    try:
        assert app.test_client().post("/api/dev/run-scheduler", base_url=BASE, json={}).status_code == 404
    finally:
        app.extensions["db_engine"].dispose()


def test_customer_app_has_no_admin_routes(schema_engine):
    app = create_app("test", overrides={"DB_SEARCH_PATH": schema_engine[1]})
    try:
        assert not any(r.rule.startswith("/api/admin") for r in app.url_map.iter_rules())
    finally:
        app.extensions["db_engine"].dispose()


def test_admin_app_does_not_import_customer_auth():
    """In a fresh interpreter (other tests import app.auth), building the admin app must not
    load app.auth or customer_auth."""
    code = ("import sys; from app.admin import create_admin_app; create_admin_app('test'); "
            "bad = [m for m in ('app.auth', 'customer_auth') if m in sys.modules]; print(bad); sys.exit(1 if bad else 0)")
    r = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_run_admin_binds_to_127_0_0_1_only():
    src = (ROOT / "run_admin.py").read_text()
    assert 'host="127.0.0.1"' in src and "0.0.0.0" not in src.replace("never 0.0.0.0", "")
