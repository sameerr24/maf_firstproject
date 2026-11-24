"""Shared fixtures for the subscription module tests: a throwaway PostgreSQL schema on the
local test database with Sameer's tables + the subscription tables and a little seed data.
Skipped when TEST_DATABASE_URL isn't set.
"""
import datetime as dt
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models import Inventory, SubscriptionsBase
from database import connection
from database.models import Base, Customer, Product

TODAY = dt.date(2026, 11, 24)      # a Tuesday
ME, OTHER, NOWA = "CUST-0001", "CUST-0002", "CUST-0003"   # NOWA: no WhatsApp
MILK, SOAP, TV = "bb-milk", "bb-soap", "bb-tv"


# ------------------------------------------------------------------ fixtures
@pytest.fixture(scope="module")
def admin_engine():
    try:
        url = connection.get_database_url("TEST_DATABASE_URL")
    except connection.DatabaseConfigError as e:
        pytest.skip(f"TEST_DATABASE_URL not set: {str(e).splitlines()[0]}")
    if "test" not in (url.database or "").lower():
        pytest.fail("TEST_DATABASE_URL must name a database containing 'test'")
    eng = connection.make_engine(url)
    try:
        connection.check_connection(eng)
    except connection.DatabaseConfigError as e:
        pytest.skip(str(e).splitlines()[0])
    yield eng
    eng.dispose()


def _product(pid, sku, ptype, price, discount, replenishable=True):
    return Product(product_id=pid, sku_id=sku, product_type=ptype, product_name=ptype.title(),
                   original_product_name=ptype, brand="Brand", original_brand="Brand", price=Decimal(price),
                   discount_price=Decimal(discount), pack_size="1 pc", category="Cat", subcategory="Sub",
                   image_url=f"https://img/{pid}", product_url=f"https://p/{pid}", replenishable=replenishable,
                   selection_method="auto", source_row_number=sku)


@pytest.fixture
def schema_engine(admin_engine):
    """(engine, schema) for a fresh seeded schema, dropped after the test."""
    schema = f"pytest_sub_{uuid.uuid4().hex[:10]}"
    with admin_engine.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    eng = connection.make_engine(admin_engine.url, search_path=schema)
    try:
        Base.metadata.create_all(eng)
        SubscriptionsBase.metadata.create_all(eng)
        with Session(eng) as s:
            s.add_all([
                _product(MILK, 1, "milk", "30.00", "27.00"),
                _product(SOAP, 2, "soap", "99.00", "99.00"),
                _product(TV, 3, "tv", "20000.00", "18000.00", replenishable=False),
                Customer(customer_id=ME, signup_date=TODAY, behaviour_segment="regular",
                         household_size=2, preferred_weekday="Sat",
                         phone_e164="+919800000001", whatsapp_opt_in=True),
                Customer(customer_id=OTHER, signup_date=TODAY, behaviour_segment="regular",
                         household_size=1, preferred_weekday="Mon",
                         phone_e164="+919800000002", whatsapp_opt_in=True),
                Customer(customer_id=NOWA, signup_date=TODAY, behaviour_segment="regular",
                         household_size=1, preferred_weekday="Wed"),
            ])
            s.flush()
            s.add_all([Inventory(product_id=MILK, available_qty=5), Inventory(product_id=SOAP, available_qty=0)])
            s.commit()
        yield eng, schema
    finally:
        eng.dispose()
        with admin_engine.begin() as conn:
            conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))


@pytest.fixture
def session(schema_engine):
    with Session(schema_engine[0], expire_on_commit=False) as s:
        yield s
