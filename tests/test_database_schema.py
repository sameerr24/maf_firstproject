"""Database-phase tests that need NO PostgreSQL server: models, CSV validation, config."""
import re
import shutil
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

from database import connection
from database.models import (
    LOAD_ORDER, Base, Customer, CustomerProductProfile, Order, OrderItem, Product, app_columns, csv_columns,
)
from database.sources import SOURCE_FILES, SourceValidationError, convert_value, file_sha256, load_sources

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_TABLES = ["products", "customers", "orders", "order_items", "customer_product_profiles"]


# ------------------------------------------------------------------ models
def test_five_tables_in_dependency_order():
    assert [m.__tablename__ for m in LOAD_ORDER] == EXPECTED_TABLES
    assert set(Base.metadata.tables) == set(EXPECTED_TABLES)
    # every FK points to a table loaded earlier
    for i, m in enumerate(LOAD_ORDER):
        for fk in m.__table__.foreign_keys:
            assert fk.column.table.name in EXPECTED_TABLES[:i]


@pytest.mark.parametrize("model", LOAD_ORDER, ids=lambda m: m.__tablename__)
def test_model_columns_match_csv_header_exactly(model):
    header = list(pd.read_csv(ROOT / SOURCE_FILES[model], nrows=0).columns)
    assert [c.name for c in csv_columns(model.__table__)] == header


def test_app_managed_columns():
    """Columns added by the subscriptions migration: not in the CSVs, and loadable without them."""
    app = {m.__tablename__: [c.name for c in app_columns(m.__table__)] for m in LOAD_ORDER}
    assert app == {"products": [], "customers": ["customer_name", "phone_e164", "whatsapp_opt_in"],
                   "orders": ["source", "delivery_slot"], "order_items": [], "customer_product_profiles": []}
    for m in LOAD_ORDER:
        for c in app_columns(m.__table__):
            assert c.nullable or c.server_default is not None, f"{c} must be nullable or have a server default"


def test_primary_keys():
    pks = {m.__tablename__: [c.name for c in m.__table__.primary_key.columns] for m in LOAD_ORDER}
    assert pks == {
        "products": ["product_id"], "customers": ["customer_id"], "orders": ["order_id"],
        "order_items": ["order_item_id"], "customer_product_profiles": ["customer_id", "product_id"],
    }


def _fks(model):
    return {(tuple(c.name for c in fk.columns), fk.referred_table.name, tuple(e.column.name for e in fk.elements))
            for fk in model.__table__.foreign_key_constraints}


def test_foreign_keys():
    assert _fks(Order) == {(("customer_id",), "customers", ("customer_id",))}
    assert _fks(OrderItem) == {
        (("order_id",), "orders", ("order_id",)),
        (("product_id",), "products", ("product_id",)),
        (("product_id", "product_type"), "products", ("product_id", "product_type")),
    }
    assert _fks(CustomerProductProfile) == {
        (("customer_id",), "customers", ("customer_id",)),
        (("product_id",), "products", ("product_id",)),
        (("product_id", "product_type"), "products", ("product_id", "product_type")),
    }
    assert _fks(Product) == _fks(Customer) == set()


def test_unique_and_check_constraints():
    def uniques(m):
        t = m.__table__
        return {tuple(c.name for c in con.columns) for con in t.constraints if con.__class__.__name__ == "UniqueConstraint"}
    assert {("sku_id",), ("product_type",), ("product_url",), ("product_id", "product_type")} <= uniques(Product)
    assert ("order_id", "product_id") in uniques(OrderItem)
    checks = {con.name for m in LOAD_ORDER for con in m.__table__.constraints if con.__class__.__name__ == "CheckConstraint"}
    for name in ["ck_order_items_quantity", "ck_order_items_line_total", "ck_products_discount_price",
                 "ck_orders_date_matches_datetime", "ck_profiles_active_period"]:
        assert name in checks


def test_relationships_are_navigable():
    rels = {m.__name__: set(sa_inspect(m).relationships.keys()) for m in LOAD_ORDER}
    assert rels["Customer"] == {"orders", "profiles"}
    assert rels["Order"] == {"customer", "items"}
    assert rels["OrderItem"] == {"order", "product"}
    assert rels["Product"] == {"order_items", "profiles"}
    assert rels["CustomerProductProfile"] == {"customer", "product"}


def test_ddl_compiles_for_postgresql():
    dialect = postgresql.dialect()
    for table in Base.metadata.sorted_tables:
        ddl = str(CreateTable(table).compile(dialect=dialect))
        assert table.name in ddl
        for idx in table.indexes:
            str(CreateIndex(idx).compile(dialect=dialect))
    ddl = str(CreateTable(Base.metadata.tables["order_items"]).compile(dialect=dialect))
    assert "NUMERIC(10, 2)" in ddl and "FOREIGN KEY(product_id, product_type)" in ddl
    assert "TIMESTAMP WITHOUT TIME ZONE" in str(CreateTable(Base.metadata.tables["orders"]).compile(dialect=dialect))


# ------------------------------------------------------------------ value conversion
@pytest.mark.parametrize("model,col,raw,expected", [
    (Product, "replenishable", "True", True),
    (Product, "replenishable", "false", False),
    (Product, "price", "194.35", Decimal("194.35")),
    (Order, "item_count", "3", 3),
    (Order, "order_date", "2026-05-20", __import__("datetime").date(2026, 5, 20)),
    (Product, "pack_unit", "", None),
])
def test_convert_value_ok(model, col, raw, expected):
    assert convert_value(raw, model.__table__.c[col]) == expected


@pytest.mark.parametrize("model,col,raw", [
    (Product, "replenishable", "yes"),
    (Product, "price", "1.234"),          # more decimals than NUMERIC(10,2): never silently rounded
    (Product, "price", "abc"),
    (Product, "price", "nan"),
    (Order, "item_count", "2.5"),
    (Order, "order_date", "2026-13-01"),
    (Order, "order_date", "2026-05-20T10:00:00"),
    (Customer, "preferred_weekday", "Monday"),  # longer than VARCHAR(3)
])
def test_convert_value_rejects(model, col, raw):
    with pytest.raises(ValueError):
        convert_value(raw, model.__table__.c[col])


# ------------------------------------------------------------------ CSV validation
@pytest.fixture(scope="module")
def real_sources():
    return load_sources()


def test_real_csvs_validate(real_sources):
    counts = {k: len(v.records) for k, v in real_sources.items()}
    assert counts == {k: len(pd.read_csv(ROOT / SOURCE_FILES[m])) for m in LOAD_ORDER for k in [m.__tablename__]}
    assert list(real_sources) == EXPECTED_TABLES


def test_reading_does_not_modify_sources():
    before = {m: file_sha256(ROOT / p) for m, p in SOURCE_FILES.items()}
    load_sources()
    assert before == {m: file_sha256(ROOT / p) for m, p in SOURCE_FILES.items()}


@pytest.fixture
def csv_copy(tmp_path):
    """Copy the 5 CSVs into tmp_path; return (overrides dict, {table: DataFrame-as-text})."""
    overrides, frames = {}, {}
    for m, rel in SOURCE_FILES.items():
        dst = tmp_path / Path(rel).name
        shutil.copy(ROOT / rel, dst)
        overrides[m.__tablename__] = dst
        frames[m.__tablename__] = pd.read_csv(dst, dtype=str, keep_default_na=False)
    return overrides, frames


def _write(overrides, frames, table):
    frames[table].to_csv(overrides[table], index=False)


@pytest.mark.parametrize("table,mutate,message", [
    ("orders", lambda df: df.drop(columns=["order_total"]), "missing required column"),
    ("customers", lambda df: df.assign(nickname="x"), "unexpected column"),
    ("customers", lambda df: pd.concat([df, df.head(1)]), "duplicate primary key"),
    ("orders", lambda df: df.assign(customer_id=df["customer_id"].where(df.index != 0, "CUST-9999")), "not found in customers"),
    ("order_items", lambda df: df.assign(product_id=df["product_id"].where(df.index != 0, "bb-0")), "not found in products"),
    ("order_items", lambda df: df.assign(product_type=df["product_type"].where(df.index != 0, "caviar")),
     "product_id, product_type"),
    ("order_items", lambda df: df.assign(quantity=df["quantity"].where(df.index != 0, "-2")), "quantity > 0"),
    ("orders", lambda df: df.assign(order_date=df["order_date"].where(df.index != 0, "2026-02-30")), "order_date"),
    ("customers", lambda df: df.assign(signup_date=df["signup_date"].where(df.index != 0, "")), "required value is empty"),
    ("customer_product_profiles", lambda df: pd.concat([df, df.head(1)]), "duplicate primary key"),
    ("order_items", lambda df: pd.concat([df, df.head(1).assign(order_item_id="OI-X")]), "uq_order_items_order_product"),
])
def test_invalid_csv_is_rejected(csv_copy, table, mutate, message):
    overrides, frames = csv_copy
    frames[table] = mutate(frames[table])
    _write(overrides, frames, table)
    with pytest.raises(SourceValidationError) as exc:
        load_sources(overrides=overrides)
    assert message in str(exc.value)


def test_missing_file_is_reported(csv_copy):
    overrides, _ = csv_copy
    overrides["orders"].unlink()
    with pytest.raises(SourceValidationError, match="source file not found"):
        load_sources(overrides=overrides)


def test_dry_run_cli_needs_no_database(capsys):
    from scripts import load_data
    assert load_data.main(["--dry-run"]) == 0
    assert "Database not touched" in capsys.readouterr().out


# ------------------------------------------------------------------ configuration & security
def test_missing_database_url_gives_clear_error(monkeypatch, tmp_path):
    monkeypatch.setattr(connection, "ENV_FILE", tmp_path / "nope.env")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(connection.DatabaseConfigError, match="DATABASE_URL is not set"):
        connection.get_database_url()


def test_non_postgres_url_rejected(monkeypatch, tmp_path):
    monkeypatch.setattr(connection, "ENV_FILE", tmp_path / "nope.env")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///x.db")
    with pytest.raises(connection.DatabaseConfigError, match="must point to PostgreSQL"):
        connection.get_database_url()


def test_password_is_masked():
    masked = connection.safe_url("postgresql+psycopg://bob:s3cret@localhost:5432/db")
    assert "s3cret" not in masked and "***" in masked


def test_no_hardcoded_credentials_in_code():
    pattern = re.compile(r"postgres(?:ql)?(?:\+\w+)?://[^\s:/@<]+:[^\s@<>]+@")
    for path in list((ROOT / "database").glob("*.py")) + list((ROOT / "scripts").glob("*.py")):
        assert not pattern.search(path.read_text(encoding="utf-8")), f"credentials in {path.name}"


def test_env_file_is_git_ignored_and_example_exists():
    ignore = (ROOT / ".gitignore").read_text().splitlines()
    assert ".env" in ignore and "!.env.example" in ignore
    example = (ROOT / ".env.example").read_text()
    assert "DATABASE_URL=" in example and "TEST_DATABASE_URL=" in example
