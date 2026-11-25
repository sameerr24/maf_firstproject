"""SqlPurchaseRepository against PostgreSQL.

Same safety rules as test_database_integration.py (its fixtures are reused): only
TEST_DATABASE_URL, a throwaway schema per test, skipped when no test database is set.
"""
import datetime as dt

import pytest
from sqlalchemy import text

from recommendation_engine import (
    InMemoryPurchaseRepository, ProductInfo, PurchaseRecord, RecommendationEngine, load_config,
)
from recommendation_engine.data.sql_repository import SqlPurchaseRepository
from recommendation_engine.output import result_to_dict
from tests.test_database_integration import admin_engine, engine, loaded, sources  # noqa: F401  (fixtures)

PRODUCT_FIELDS = ("product_id", "product_name", "brand", "product_type", "category", "subcategory",
                  "consumption_pattern", "pack_size", "image_url", "replenishable", "price", "discount_price")


def _memory_repo(sources):  # noqa: F811
    """The same data as the database, built straight from the CSVs."""
    orders = {o["order_id"]: o for o in sources["orders"].records}
    records = [PurchaseRecord(order_id=i["order_id"], customer_id=orders[i["order_id"]]["customer_id"],
                              product_id=i["product_id"], order_date=orders[i["order_id"]]["order_date"],
                              quantity=i["quantity"], order_item_id=i["order_item_id"])
               for i in sources["order_items"].records]
    products = [ProductInfo(**{k: p[k] for k in PRODUCT_FIELDS}) for p in sources["products"].records]
    return InMemoryPurchaseRepository([c["customer_id"] for c in sources["customers"].records], records, products)


def test_sql_and_in_memory_repositories_give_identical_recommendations(loaded, sources):  # noqa: F811
    config = load_config()
    sql_engine = RecommendationEngine(repository=SqlPurchaseRepository(loaded), config=config)
    mem_engine = RecommendationEngine(repository=_memory_repo(sources), config=config)
    for n in range(1, 101, 7):
        cid = f"CUST-{n:04d}"
        assert (result_to_dict(sql_engine.generate_recommendations(cid, limit=50))
                == result_to_dict(mem_engine.generate_recommendations(cid, limit=50))), cid


def test_subscription_orders_are_not_purchase_history(loaded):  # noqa: F811
    repo = SqlPurchaseRepository(loaded)
    before = len(repo.get_purchase_records("CUST-0001"))
    latest = repo.get_latest_purchase_date()
    with loaded.begin() as conn:
        conn.execute(text("INSERT INTO orders (order_id, customer_id, order_date, order_datetime, item_count, "
                          "unit_count, order_total, source) VALUES ('SUB-1', 'CUST-0001', '2026-11-20', "
                          "'2026-11-20 09:00', 1, 2, 56, 'subscription')"))
        conn.execute(text("INSERT INTO order_items VALUES ('SUBI-1', 'SUB-1', 'bb-242671', 'milk', 2, 28, 56)"))
    assert len(repo.get_purchase_records("CUST-0001")) == before
    assert repo.get_latest_purchase_date() == latest == dt.date(2026, 11, 15)


def test_live_subscriptions_are_excluded(loaded):  # noqa: F811
    repo = SqlPurchaseRepository(loaded)
    engine_ = RecommendationEngine(repository=repo, config=load_config())
    assert repo.get_live_subscription_product_ids("CUST-0001") == set()  # no subscriptions table yet
    before = {r.product_id for r in engine_.generate_recommendations("CUST-0001", limit=50).recommendations}
    milk, other = "bb-242671", sorted(before - {"bb-242671"})[0]
    assert milk in before

    with loaded.begin() as conn:  # minimal stand-in for the Flask module's table
        conn.execute(text("CREATE TABLE subscriptions (subscription_id int, customer_id varchar, "
                          "product_id varchar, status varchar)"))
        conn.execute(text(f"INSERT INTO subscriptions VALUES (1, 'CUST-0001', '{milk}', 'ACTIVE'), "
                          f"(2, 'CUST-0001', '{other}', 'CANCELLED')"))
    repo2 = SqlPurchaseRepository(loaded)
    result = RecommendationEngine(repository=repo2, config=load_config()).generate_recommendations("CUST-0001", limit=50)
    after = {r.product_id for r in result.recommendations}
    assert after == before - {milk} and other in after
    assert result.excluded_products == {"already_subscribed": 1}


def test_session_reuses_one_connection(loaded):  # noqa: F811
    repo = SqlPurchaseRepository(loaded)
    with repo.session():
        pinned = repo._pinned.get()
        assert pinned is not None
        repo.customer_exists("CUST-0001")
        repo.get_products(["bb-242671"])
        with repo.session():  # nested use keeps the same connection
            assert repo._pinned.get() is pinned
    assert repo._pinned.get() is None and pinned.closed

