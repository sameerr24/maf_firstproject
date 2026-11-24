"""HTTP layer: auth, status codes, JSON shapes, and that mutations are committed (or rolled
back on error). Business rules are covered in test_subscriptions_service.py.
Runs the real app against a throwaway schema on the local test database.
"""
import pytest

from app import create_app
from app.auth_bridge import issue_token
from subscription_db import ME, MILK, NOWA, OTHER, TV, no_real_telegram, admin_engine, schema_engine  # noqa: F401


@pytest.fixture
def app(schema_engine):
    _, schema = schema_engine
    app = create_app("test", overrides={"DB_SEARCH_PATH": schema})
    yield app
    app.extensions["db_engine"].dispose()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def auth(app):
    def headers(customer_id=ME):
        with app.app_context():
            return {"Authorization": f"Bearer {issue_token(customer_id)}"}
    return headers


def create(client, auth, customer=ME, **data):
    return client.post("/api/subscriptions", json={"product_id": MILK, **data}, headers=auth(customer))


# ------------------------------------------------------------------ auth
@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer nope"}, {"Authorization": "Basic abc"}])
@pytest.mark.parametrize("method,path", [("get", "/api/subscriptions"), ("post", "/api/subscriptions"),
                                         ("get", f"/api/products/{MILK}"),
                                         ("get", f"/api/subscriptions/draft?product_id={MILK}"),
                                         ("patch", "/api/subscriptions/1"), ("delete", "/api/subscriptions/1"),
                                         ("post", "/api/subscriptions/1/skip")])
def test_requires_auth(client, headers, method, path):
    r = getattr(client, method)(path, headers=headers, json={})
    assert r.status_code == 401
    assert r.json["error"]["code"] == "unauthorized"


def test_token_signed_with_other_key_is_rejected(client, app):
    other = create_app("test", overrides={"SECRET_KEY": "different", "DB_SEARCH_PATH": "public"})
    with other.app_context():
        token = issue_token(ME)
    other.extensions["db_engine"].dispose()
    assert client.get("/api/subscriptions", headers={"Authorization": f"Bearer {token}"}).status_code == 401


# ------------------------------------------------------------------ products / draft
def test_product_page(client, auth):
    r = client.get(f"/api/products/{MILK}", headers=auth())
    assert r.status_code == 200
    p = r.json["product"]
    assert (p["regular_price"], p["subscriber_price"], p["saving_per_unit"]) == ("30.00", "27.00", "3.00")
    assert p["subscription_id"] is None


def test_unknown_product_is_404_json(client, auth):
    r = client.get("/api/products/nope", headers=auth())
    assert r.status_code == 404 and r.json == {"error": {"code": "not_found", "message": "Product not found"}}


def test_draft(client, auth):
    r = client.get(f"/api/subscriptions/draft?product_id={MILK}", headers=auth())
    assert r.status_code == 200 and r.json["draft"]["quantity"] == 1
    assert client.get("/api/subscriptions/draft", headers=auth()).status_code == 400
    r = client.get(f"/api/subscriptions/draft?product_id={TV}", headers=auth())
    assert r.status_code == 400 and r.json["error"]["code"] == "not_subscribable"


# ------------------------------------------------------------------ create / read
def test_create_then_list_and_get(client, auth):
    r = create(client, auth, quantity=2)
    assert r.status_code == 201
    sub = r.json["subscription"]
    assert r.headers["Location"] == f"/api/subscriptions/{sub['subscription_id']}"
    assert client.get(r.headers["Location"], headers=auth()).json["subscription"]["quantity"] == 2
    assert [s["subscription_id"] for s in client.get("/api/subscriptions", headers=auth()).json["subscriptions"]] \
        == [sub["subscription_id"]]
    assert client.get(f"/api/products/{MILK}", headers=auth()).json["product"]["subscription_id"] == \
        sub["subscription_id"]


def test_create_errors(client, auth):
    assert create(client, auth, quantity=0).status_code == 400
    r = create(client, auth, customer_id=OTHER)
    assert r.status_code == 400 and r.json["error"]["fields"] == ["customer_id"]
    r = client.post("/api/subscriptions", data="not json", headers=auth(), content_type="application/json")
    assert r.status_code == 400
    assert create(client, auth).status_code == 201
    r = create(client, auth)
    assert r.status_code == 409 and r.json["error"]["code"] == "already_subscribed"


def test_other_customers_subscription_is_404(client, auth):
    sid = create(client, auth).json["subscription"]["subscription_id"]
    for method, path in [("get", f"/api/subscriptions/{sid}"), ("patch", f"/api/subscriptions/{sid}"),
                         ("delete", f"/api/subscriptions/{sid}"), ("post", f"/api/subscriptions/{sid}/skip")]:
        r = getattr(client, method)(path, headers=auth(OTHER), json={"quantity": 2})
        assert r.status_code == 404, (method, path)
    assert client.get("/api/subscriptions", headers=auth(OTHER)).json["subscriptions"] == []
    assert client.get(f"/api/subscriptions/{sid}", headers=auth()).json["subscription"]["quantity"] == 1


# ------------------------------------------------------------------ mutations persist / roll back
def test_patch_pause_resume_skip_cancel_persist(client, auth):
    sid = create(client, auth).json["subscription"]["subscription_id"]
    url = f"/api/subscriptions/{sid}"
    assert client.patch(url, json={"quantity": 5}, headers=auth()).status_code == 200
    assert client.get(url, headers=auth()).json["subscription"]["quantity"] == 5

    assert client.patch(url, json={"status": "PAUSED"}, headers=auth()).json["subscription"]["status"] == "PAUSED"
    assert client.get(url, headers=auth()).json["subscription"]["status"] == "PAUSED"
    assert client.post(f"{url}/skip", headers=auth()).status_code == 409   # paused
    assert client.patch(url, json={"status": "ACTIVE"}, headers=auth()).json["subscription"]["status"] == "ACTIVE"

    before = client.get(url, headers=auth()).json["subscription"]["next_delivery_date"]
    r = client.post(f"{url}/skip", headers=auth())
    assert r.status_code == 200 and r.json["skipped_date"] == before
    assert client.get(url, headers=auth()).json["subscription"]["next_delivery_date"] > before

    r = client.delete(url, headers=auth())
    assert r.status_code == 200 and r.json["subscription"]["status"] == "CANCELLED"
    assert client.get("/api/subscriptions", headers=auth()).json["subscriptions"] == []
    assert len(client.get("/api/subscriptions?include_cancelled=1", headers=auth()).json["subscriptions"]) == 1


def test_failed_patch_changes_nothing(client, auth):
    sid = create(client, auth).json["subscription"]["subscription_id"]
    url = f"/api/subscriptions/{sid}"
    # quantity is applied before frequency_days fails validation; the request must roll back
    r = client.patch(url, json={"quantity": 5, "frequency_days": 0}, headers=auth())
    assert r.status_code == 400 and r.json["error"]["field"] == "frequency_days"
    assert client.get(url, headers=auth()).json["subscription"]["quantity"] == 1


def test_subscribe_without_whatsapp_is_409(client, auth):
    r = create(client, auth, customer=NOWA)
    assert r.status_code == 409 and r.json["error"]["code"] == "messaging_required"
    assert client.get(f"/api/subscriptions/draft?product_id={MILK}", headers=auth(NOWA)).json["draft"]["messaging_ready"] is False
