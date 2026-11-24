"""Shubham's models: no database needed."""
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import configure_mappers
from sqlalchemy.schema import CreateIndex, CreateTable

import app.models as m
from database.models import Base


def test_mappers_configure_and_sameer_metadata_untouched():
    configure_mappers()
    assert set(Base.metadata.tables) == {"products", "customers", "orders", "order_items", "customer_product_profiles"}


def test_ddl_compiles_for_postgres():
    for t in m.SubscriptionsBase.metadata.sorted_tables:
        str(CreateTable(t).compile(dialect=postgresql.dialect()))
        for ix in t.indexes:
            str(CreateIndex(ix).compile(dialect=postgresql.dialect()))


def test_live_subscription_index_is_partial():
    ix = next(i for i in m.Subscription.__table__.indexes if i.name == "uq_live_subscription")
    sql = str(CreateIndex(ix).compile(dialect=postgresql.dialect()))
    assert "UNIQUE" in sql and "WHERE status <> 'CANCELLED'" in sql
