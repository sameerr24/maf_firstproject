"""FastAPI layer tests. The engine runs on an in-memory repository: no database, no server."""
import pytest
from fastapi.testclient import TestClient
import datetime as dt

from customer_auth import AuthSettings, TokenService
from recommendation_api.factory import create_app
from recommendation_api.service import RecommendationService
from recommendation_api.settings import ApiSettings
from recommendation_engine import DataSourceError, RecommendationEngine, load_config
from recommendation_engine.output import result_to_dict
from tests.recommendation_fixtures import REF, product, purchases, repo

AUTH = AuthSettings(secret_key="t" * 40, access_token_ttl_seconds=3600)
SETTINGS = ApiSettings(auth=AUTH, cors_origins=("http://localhost:5173",), log_level="WARNING")
TOKENS = TokenService(AUTH)


def auth(customer_id="CUST-1"):
    return {"Authorization": f"Bearer {TOKENS.issue(customer_id)}"}


def _data():
    products = [product(f"P{i:02d}", name=f"Item {i}", image_url=f"https://img/{i}.jpg") for i in range(12)]
    records = []
    for i in range(12):
        records += purchases("CUST-1", f"P{i:02d}", [41 - i, 31 - i, 21 - i, 11 - i])  # newest: REF
    return repo(records, products, customers=("CUST-1", "CUST-EMPTY"))


@pytest.fixture
def engine():
    return RecommendationEngine(repository=_data(), config=load_config())


@pytest.fixture
def client(engine):
    return TestClient(create_app(SETTINGS, service=RecommendationService(engine)), headers=auth())


def test_recommendations_endpoint(client, engine):
    r = client.get("/api/v1/recommendations/CUST-1")
    assert r.status_code == 200
    body = r.json()
    assert body["customer_id"] == "CUST-1" and body["total_recommendations"] == 10  # default limit
    assert body["total_eligible_products"] == 12
    recs = body["recommendations"]
    assert [x["rank"] for x in recs] == list(range(1, 11))
    assert [x["final_score"] for x in recs] == sorted((x["final_score"] for x in recs), reverse=True)
    assert recs[0]["product_image"].startswith("https://img/") and recs[0]["explanation"]
    # same engine, same numbers: the API adds no logic of its own
    assert body == result_to_dict(engine.generate_recommendations("CUST-1"))


def test_limit_and_as_of(client):
    assert len(client.get("/api/v1/recommendations/CUST-1?limit=3").json()["recommendations"]) == 3
    body = client.get("/api/v1/recommendations/CUST-1?as_of=2026-11-01&limit=50").json()
    assert body["reference_date"] == "2026-11-01"


def test_customer_without_purchases_gets_empty_list(client):
    body = client.get("/api/v1/recommendations/CUST-EMPTY", headers=auth("CUST-EMPTY")).json()
    assert body["total_recommendations"] == 0 and body["recommendations"] == []


@pytest.mark.parametrize("url,status,code", [
    ("/api/v1/recommendations/CUST-404", 403, "forbidden"),
    ("/api/v1/recommendations/CUST-1?limit=0", 422, "validation_error"),
    ("/api/v1/recommendations/CUST-1?limit=abc", 422, "validation_error"),
    ("/api/v1/recommendations/CUST-1?limit=51", 422, "invalid_request"),
    ("/api/v1/recommendations/CUST-1?as_of=2026-02-30", 422, "validation_error"),
    ("/api/v1/recommendations/" + "x" * 65, 422, "validation_error"),
    ("/api/v1/nothing-here", 404, "not_found"),
])
def test_errors_use_one_json_shape(client, url, status, code):
    r = client.get(url)
    assert r.status_code == status
    err = r.json()["error"]
    assert err["code"] == code and err["message"] and err["request_id"] == r.headers["X-Request-ID"]


class _BrokenRepo:
    def __init__(self, exc):
        self.exc = exc

    def customer_exists(self, customer_id):
        raise self.exc


@pytest.mark.parametrize("exc,status,code", [
    (DataSourceError("database query failed (OperationalError)"), 503, "service_unavailable"),
    (RuntimeError("secret host db.internal:5432 password=hunter2"), 500, "internal_error"),
])
def test_internal_failures_do_not_leak_details(exc, status, code):
    service = RecommendationService(RecommendationEngine(repository=_BrokenRepo(exc), config=load_config()))
    client = TestClient(create_app(SETTINGS, service=service), raise_server_exceptions=False, headers=auth())
    r = client.get("/api/v1/recommendations/CUST-1")
    assert r.status_code == status and r.json()["error"]["code"] == code
    assert "hunter2" not in r.text and "db.internal" not in r.text and "Traceback" not in r.text


def test_request_id_timing_and_cors_headers(client):
    r = client.get("/api/v1/health", headers={"X-Request-ID": "abc-123", "Origin": "http://localhost:5173"})
    assert r.json() == {"status": "ok", "service": "recommendation-api"}
    assert r.headers["X-Request-ID"] == "abc-123" and float(r.headers["X-Process-Time-Ms"]) >= 0
    assert r.headers["access-control-allow-origin"] == "http://localhost:5173"
    other = client.get("/api/v1/health", headers={"Origin": "http://evil.example"})
    assert "access-control-allow-origin" not in other.headers
    bad_id = client.get("/api/v1/health", headers={"X-Request-ID": "x" * 100 + "\n"})
    assert len(bad_id.headers["X-Request-ID"]) == 16  # unsafe ids are replaced


def test_creating_the_app_does_not_touch_the_database():
    app = create_app(SETTINGS)  # no service injected: built lazily on first request
    assert app.state.recommendation_service is None
    assert TestClient(app).get("/api/v1/health").status_code == 200


def test_openapi_documents_the_endpoint(client):
    spec = client.get("/openapi.json").json()
    assert "/api/v1/recommendations/{customer_id}" in spec["paths"]


def test_settings_from_env(monkeypatch):
    monkeypatch.setenv("AUTH_SECRET_KEY", "k" * 40)
    monkeypatch.setenv("REC_API_CORS_ORIGINS", "http://a.test, http://b.test")
    monkeypatch.setenv("REC_API_DOCS", "false")
    monkeypatch.setenv("REC_API_PORT", "8123")
    s = ApiSettings.from_env()
    assert s.cors_origins == ("http://a.test", "http://b.test") and not s.docs_enabled and s.port == 8123
    assert TestClient(create_app(s)).get("/docs").status_code == 404
    monkeypatch.setenv("REC_API_PORT", "eighty")
    with pytest.raises(ValueError):
        ApiSettings.from_env()


def test_reference_date_in_response_matches_engine_default(client):
    assert client.get("/api/v1/recommendations/CUST-1").json()["reference_date"] == REF.isoformat()


# ------------------------------------------------------------------ authentication
@pytest.fixture
def anon(engine):
    """Client without an Authorization header."""
    return TestClient(create_app(SETTINGS, service=RecommendationService(engine)))


@pytest.mark.parametrize("headers,code", [
    ({}, "unauthorized"),
    ({"Authorization": "Bearer not-a-token"}, "unauthorized"),
    ({"Authorization": "Basic dXNlcjpwYXNz"}, "unauthorized"),
    ({"Authorization": "Bearer " + TokenService(AuthSettings(secret_key="x" * 40)).issue("CUST-1")}, "unauthorized"),
])
@pytest.mark.parametrize("url", ["/api/v1/recommendations/CUST-1", "/api/v1/recommendations/me", "/api/v1/auth/me"])
def test_protected_routes_require_a_valid_token(anon, url, headers, code):
    r = anon.get(url, headers=headers)
    assert r.status_code == 401 and r.headers["WWW-Authenticate"] == "Bearer"
    assert r.json()["error"]["code"] == code


def test_expired_token(anon, monkeypatch):
    token = TOKENS.issue("CUST-1", now=dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=AUTH.access_token_ttl_seconds + 5))
    r = anon.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401 and r.json()["error"]["code"] == "token_expired"


def test_customers_only_see_their_own_recommendations(anon):
    r = anon.get("/api/v1/recommendations/CUST-EMPTY", headers=auth("CUST-1"))
    assert r.status_code == 403 and r.json()["error"]["code"] == "forbidden"
    mine = anon.get("/api/v1/recommendations/me?limit=2", headers=auth("CUST-1")).json()
    assert mine["customer_id"] == "CUST-1" and len(mine["recommendations"]) == 2
    # a valid token for a customer that no longer exists
    r = anon.get("/api/v1/recommendations/CUST-GONE", headers=auth("CUST-GONE"))
    assert r.status_code == 404 and r.json()["error"]["code"] == "customer_not_found"


def test_customer_id_demo_login_route_is_removed(anon):
    response = anon.post("/api/v1/auth/demo-login", json={"customer_id": "CUST-1"})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_app_refuses_to_start_without_auth(monkeypatch):
    from customer_auth import AuthConfigError
    with pytest.raises(AuthConfigError):
        create_app(ApiSettings(log_level="WARNING"))
    monkeypatch.setenv("AUTH_SECRET_KEY", "")
    with pytest.raises(AuthConfigError):
        ApiSettings.from_env()


def test_cors_preflight_allows_authorization_header(anon):
    r = anon.options("/api/v1/recommendations/me", headers={
        "Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET",
        "Access-Control-Request-Headers": "authorization"})
    assert r.status_code == 200 and "authorization" in r.headers["access-control-allow-headers"].lower()


# ------------------------------------------------------------------ prices
def test_prices_are_passed_through():
    from decimal import Decimal
    eng = RecommendationEngine(repository=repo(purchases("CUST-1", "A", [20, 10, 0]),
                                                [product("A", price=Decimal("60.00"), discount_price=Decimal("54.50"))],
                                                customers=("CUST-1",)), config=load_config())
    client = TestClient(create_app(SETTINGS, service=RecommendationService(eng)), headers=auth())
    rec = client.get("/api/v1/recommendations/me").json()["recommendations"][0]
    assert (rec["price"], rec["discount_price"]) == (60.0, 54.5)
