"""PostgreSQL integration tests.

Safety:
  * Uses ONLY ``TEST_DATABASE_URL`` (never ``DATABASE_URL``). Tests are skipped if it
    is unset or unreachable.
  * The database name must contain "test", and it must differ from DATABASE_URL.
  * Each test runs in its own temporary schema (``pytest_<random>``), which is dropped
    afterwards, so even the test database's ``public`` schema is untouched.
"""
import copy
import os
import uuid

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from database import connection
from database.models import LOAD_ORDER, Customer, Order, OrderItem, Product
from database.sources import load_sources
from scripts import create_database, load_data, validate_database as vdb


def _test_url():
    try:
        url = connection.get_database_url("TEST_DATABASE_URL")
    except connection.DatabaseConfigError as e:
        pytest.skip(f"PostgreSQL integration tests skipped: {str(e).splitlines()[0]}")
    if "test" not in (url.database or "").lower():
        pytest.fail(f"Refusing to run: TEST_DATABASE_URL database '{url.database}' must contain 'test'.")
    main = os.getenv("DATABASE_URL")
    if main:
        m = make_url(main)
        if (m.host, m.port, m.database) == (url.host, url.port, url.database):
            pytest.fail("Refusing to run: TEST_DATABASE_URL points at the same database as DATABASE_URL.")
    return url


@pytest.fixture(scope="session")
def admin_engine():
    url = _test_url()
    try:
        eng = connection.make_engine(url)
    except ImportError as e:  # e.g. psycopg not installed
        pytest.skip(f"PostgreSQL integration tests skipped: driver not installed ({e})")
    try:
        connection.check_connection(eng)
    except connection.DatabaseConfigError as e:
        eng.dispose()
        pytest.skip(f"PostgreSQL integration tests skipped: {str(e).splitlines()[0]}")
    yield eng
    eng.dispose()


@pytest.fixture
def engine(admin_engine):
    """Engine bound to a fresh, empty schema that is dropped after the test."""
    schema = f"pytest_{uuid.uuid4().hex[:12]}"
    with admin_engine.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    eng = connection.make_engine(admin_engine.url, search_path=schema)
    try:
        yield eng
    finally:
        eng.dispose()
        with admin_engine.begin() as conn:
            conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))


@pytest.fixture(scope="session")
def sources():
    return load_sources()


@pytest.fixture
def loaded(engine, sources):
    create_database.create_tables(engine)
    create_database.enable_rls(engine)
    load_data.load_all(engine, sources, log=lambda *_: None)
    return engine


def counts(engine):
    with engine.connect() as conn:
        return load_data.existing_counts(conn)


def dump(engine):
    with engine.connect() as conn:
        return {m.__tablename__: sorted(map(tuple, conn.execute(select(m.__table__)).all()), key=str)
                for m in LOAD_ORDER}


# ------------------------------------------------------------------ table creation
def test_isolated_schema_is_used(engine):
    info = connection.check_connection(engine)
    assert info["schema"].startswith("pytest_")


def test_create_tables(engine):
    assert create_database.schema_status(engine)["missing"] == [m.__tablename__ for m in LOAD_ORDER]
    created = create_database.create_tables(engine)
    assert created == [m.__tablename__ for m in LOAD_ORDER]
    status = create_database.schema_status(engine)
    assert status["missing"] == [] and status["column_mismatches"] == {}


def test_rls_enabled_and_owner_still_has_full_access(engine, sources):
    create_database.create_tables(engine)
    assert set(create_database.rls_status(engine).values()) == {False}
    assert create_database.enable_rls(engine) == [m.__tablename__ for m in LOAD_ORDER]
    assert set(create_database.rls_status(engine).values()) == {True}
    assert create_database.enable_rls(engine) == []  # idempotent
    load_data.load_all(engine, sources, log=lambda *_: None)  # owner bypasses RLS
    assert counts(engine) == {k: len(v.records) for k, v in sources.items()}


def test_validator_warns_when_rls_is_off(engine, sources):
    create_database.create_tables(engine)
    load_data.load_all(engine, sources, log=lambda *_: None)
    checks = {c.name: c for c in vdb.run_checks(engine, sources)}
    rls = next(c for n, c in checks.items() if n.startswith("row-level security"))
    assert rls.status == vdb.WARN and vdb.exit_code(list(checks.values())) == 0


def test_create_tables_is_non_destructive(loaded):
    before = counts(loaded)
    assert create_database.create_tables(loaded) == []
    assert counts(loaded) == before


# ------------------------------------------------------------------ loading
def test_load_counts_match_csv(loaded, sources):
    assert counts(loaded) == {k: len(v.records) for k, v in sources.items()}


def test_orm_relationships_navigate(loaded):
    with Session(loaded) as s:
        cust = s.get(Customer, "CUST-0001")
        assert cust.orders and cust.profiles
        order = cust.orders[0]
        assert order.customer is cust and order.items
        item = order.items[0]
        assert isinstance(item.product, Product) and item.product.product_type == item.product_type
        milk = s.scalar(select(Product).where(Product.product_type == "milk"))
        n = s.scalar(select(func.count()).select_from(OrderItem).where(OrderItem.product_id == milk.product_id))
        assert len(milk.order_items) == n > 0


def test_load_refuses_when_data_exists(loaded, sources):
    before = dump(loaded)
    with pytest.raises(load_data.LoadError, match="already contains data"):
        load_data.load_all(loaded, sources, log=lambda *_: None)
    assert dump(loaded) == before


def test_replace_is_idempotent(loaded, sources):
    before = dump(loaded)
    load_data.load_all(loaded, sources, replace=True, log=lambda *_: None)
    load_data.load_all(loaded, sources, replace=True, log=lambda *_: None)
    assert dump(loaded) == before


def test_failed_replace_rolls_back_completely(loaded, sources):
    """An error in the LAST table must leave the previous data fully intact."""
    before = dump(loaded)
    bad = copy.copy(sources)
    bad_profiles = copy.copy(sources["customer_product_profiles"])
    bad_profiles.records = [dict(r) for r in bad_profiles.records]
    bad_profiles.records[-1]["purchase_occasions"] = -1  # violates ck_profiles_occasions in the DB
    bad["customer_product_profiles"] = bad_profiles
    with pytest.raises(load_data.LoadError, match="ck_profiles_occasions"):
        load_data.load_all(loaded, bad, replace=True, log=lambda *_: None)
    assert dump(loaded) == before


def test_failed_initial_load_leaves_tables_empty(engine, sources):
    create_database.create_tables(engine)
    bad = copy.copy(sources)
    bad_items = copy.copy(sources["order_items"])
    bad_items.records = [dict(r) for r in bad_items.records]
    bad_items.records[100]["product_id"] = "bb-does-not-exist"
    bad["order_items"] = bad_items
    with pytest.raises(load_data.LoadError, match="constraint"):
        load_data.load_all(engine, bad, log=lambda *_: None)
    assert set(counts(engine).values()) == {0}


def test_load_requires_tables(engine, sources):
    with pytest.raises(load_data.LoadError, match="create_database.py"):
        load_data.load_all(engine, sources, log=lambda *_: None)


# ------------------------------------------------------------------ DB-level integrity
@pytest.mark.parametrize("sql", [
    "INSERT INTO orders VALUES ('ORD-X', 'CUST-9999', '2026-06-01', '2026-06-01 10:00', 1, 1, 10)",
    "INSERT INTO orders VALUES ('ORD-X', 'CUST-0001', '2026-06-01', '2026-06-02 10:00', 1, 1, 10)",
    "INSERT INTO order_items VALUES ('OI-X', 'ORD-000001', 'bb-242671', 'milk', 0, 28, 0)",
    "INSERT INTO order_items VALUES ('OI-X', 'ORD-000001', 'bb-242671', 'bread', 1, 28, 28)",
    "INSERT INTO order_items VALUES ('OI-X', 'ORD-000001', 'bb-242671', 'milk', 2, 28, 50)",
    "INSERT INTO order_items SELECT 'OI-X', order_id, product_id, product_type, quantity, unit_price, line_total "
    "FROM order_items LIMIT 1",
    "INSERT INTO customer_product_profiles SELECT * FROM customer_product_profiles LIMIT 1",
    "UPDATE products SET discount_price = price + 1 WHERE product_type = 'milk'",
    "DELETE FROM customers WHERE customer_id = 'CUST-0001'",
    "UPDATE customers SET preferred_weekday = 'Fun' WHERE customer_id = 'CUST-0001'",
])
def test_database_constraints_reject_bad_data(loaded, sql):
    with pytest.raises(IntegrityError):
        with loaded.begin() as conn:
            conn.execute(text(sql))


# ------------------------------------------------------------------ validation
def test_validation_passes_after_load(loaded, sources):
    checks = vdb.run_checks(loaded, sources)
    problems = [c for c in checks if c.status != vdb.PASS]
    assert not problems, problems
    assert vdb.exit_code(checks) == 0 and len(checks) > 40


def test_validation_fails_on_missing_tables(engine, sources):
    checks = vdb.run_checks(engine, sources)
    assert checks[0].status == vdb.FAIL and vdb.exit_code(checks) == 1


def test_validation_detects_tampering(loaded, sources):
    with loaded.begin() as conn:
        conn.execute(text("UPDATE orders SET order_total = order_total + 1 WHERE order_id = 'ORD-000001'"))
        conn.execute(text("DELETE FROM customer_product_profiles WHERE purchase_occasions > 0 "
                          "AND customer_id = 'CUST-0001' AND product_id = 'bb-242671'"))
    checks = {c.name: c for c in vdb.run_checks(loaded, sources)}
    assert checks["orders: order_total = sum(line_total) + subscription_fee"].status == vdb.FAIL
    assert checks["orders: values identical to CSV"].status == vdb.FAIL
    assert checks["customer_product_profiles: rows match CSV"].status == vdb.FAIL
    assert checks["purchased (customer, product) pairs without a profile"].status == vdb.WARN
    assert vdb.exit_code(list(checks.values())) == 1


def test_validation_warning_only_exit_codes(loaded, sources):
    with loaded.begin() as conn:  # plausible-but-unexpected: a price differing from the catalogue
        conn.execute(text("UPDATE products SET discount_price = discount_price - 1 WHERE product_type = 'rice'"))
    checks = vdb.run_checks(loaded, sources)
    warn = [c for c in checks if c.status == vdb.WARN]
    assert any("unit_price differs" in c.name for c in warn)
    assert vdb.exit_code([c for c in checks if c.category != "content"]) == 0
    assert vdb.exit_code([c for c in checks if c.category != "content"], strict=True) == 1


# ------------------------------------------------------------------ app-managed columns
def test_replace_refuses_when_app_data_exists(loaded, sources):
    with loaded.begin() as conn:
        conn.execute(text("UPDATE customers SET customer_name = 'Test' WHERE customer_id = "
                          "(SELECT min(customer_id) FROM customers)"))
    with pytest.raises(load_data.LoadError, match="App-managed data would be wiped"):
        load_data.load_all(loaded, sources, replace=True, log=lambda *_: None)
    with loaded.connect() as conn:  # rolled back: the name is still there
        assert conn.execute(text("SELECT count(customer_name) FROM customers")).scalar_one() == 1


def test_validation_ignores_app_columns_and_subscription_orders(loaded, sources):
    with loaded.begin() as conn:
        cid = conn.execute(text("SELECT min(customer_id) FROM customers")).scalar_one()
        pid, ptype, price = conn.execute(text("SELECT product_id, product_type, discount_price FROM products LIMIT 1")).one()
        conn.execute(text("UPDATE customers SET customer_name = 'Test', phone_e164 = '+447700900001' "
                          "WHERE customer_id = :c"), {"c": cid})
        conn.execute(text("UPDATE orders SET delivery_slot = '07:00-09:00'"))
        conn.execute(text("INSERT INTO orders (order_id, customer_id, order_date, order_datetime, item_count, "
                          "unit_count, order_total, source) VALUES ('SUB-1', :c, '2026-12-01', '2026-12-01 08:00', "
                          "1, 2, :t, 'subscription')"), {"c": cid, "t": price * 2})
        conn.execute(text("INSERT INTO order_items VALUES ('SUBI-1', 'SUB-1', :p, :pt, 2, :u, :t)"),
                     {"p": pid, "pt": ptype, "u": price, "t": price * 2})
    checks = vdb.run_checks(loaded, sources)
    assert [c for c in checks if c.status == vdb.FAIL] == []


def test_subscription_orders_do_not_trigger_csv_warnings(loaded, sources):
    """Subscription orders (source='subscription') are app data: a different price or a
    product outside the customer's synthetic profile must not raise CSV-consistency warnings."""
    with loaded.begin() as conn:
        pid, ptype, price = conn.execute(text(
            "SELECT product_id, product_type, discount_price + 1 FROM products p WHERE NOT EXISTS "
            "(SELECT 1 FROM customer_product_profiles f WHERE f.customer_id = 'CUST-0001' "
            "AND f.product_id = p.product_id) ORDER BY product_id LIMIT 1")).one()
        conn.execute(text("INSERT INTO orders (order_id, customer_id, order_date, order_datetime, item_count, "
                          "unit_count, order_total, source) VALUES ('SUB-1', 'CUST-0001', '2026-11-20', "
                          "'2026-11-20 09:00', 1, 2, :total, 'subscription')"), {"total": price * 2})
        conn.execute(text("INSERT INTO order_items VALUES ('SUBI-1', 'SUB-1', :pid, :ptype, 2, :price, :total)"),
                     {"pid": pid, "ptype": ptype, "price": price, "total": price * 2})
    checks = vdb.run_checks(loaded, sources)
    problems = [c for c in checks if c.status != vdb.PASS]
    assert not problems, problems
