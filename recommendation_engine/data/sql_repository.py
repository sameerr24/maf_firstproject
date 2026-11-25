"""PostgreSQL/Supabase implementation of ``PurchaseRepository``.

Reuses the project's existing database layer: ``database.connection.make_engine`` (reads
DATABASE_URL from .env, handles the Supabase pooler) and the table definitions in
``database.models``. It imports nothing from Flask (``app/``) or FastAPI.

Purchase history = ``orders JOIN order_items`` restricted to the configured
``orders.source`` values (by default only 'manual', so subscription-generated orders
are excluded). The ``subscriptions`` table belongs to the Flask subscriptions module; it
is read with plain SQL, and if it does not exist (e.g. a local database) no products
are treated as subscribed.
"""

from __future__ import annotations

import datetime as dt
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterable, Iterator

from sqlalchemy import bindparam, func, inspect, select, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import SQLAlchemyError

from database.models import Customer, Order, OrderItem, Product
from recommendation_engine.data.purchase_data import ProductInfo, PurchaseRecord
from recommendation_engine.errors import DataSourceError


class SqlPurchaseRepository:
    def __init__(self, engine: Engine | None = None, *, order_sources: Iterable[str] = ("manual",),
                 inactive_subscription_statuses: Iterable[str] = ("CANCELLED",)):
        if engine is None:
            from database.connection import make_engine  # reads DATABASE_URL lazily
            engine = make_engine()
        self.engine = engine
        self.order_sources = tuple(order_sources)
        self.inactive_subscription_statuses = tuple(inactive_subscription_statuses)
        self._has_subscriptions: bool | None = None
        self._pinned: ContextVar[Connection | None] = ContextVar(f"pinned_connection_{id(self)}", default=None)

    # -------------------------------------------------------------- helpers
    @contextmanager
    def session(self) -> Iterator[None]:
        """Run every call inside this block on ONE pooled connection.

        The engine uses it for each recommendation request: against a remote Supabase
        database this saves a pool checkout (ping + rollback round trips) per query.
        Safe with concurrent requests: the pinned connection is stored in a ContextVar.
        """
        if self._pinned.get() is not None:  # nested: reuse the outer connection
            yield
            return
        try:
            conn = self.engine.connect()
        except SQLAlchemyError as e:
            raise DataSourceError(f"database connection failed ({type(e).__name__})") from e
        token = self._pinned.set(conn)
        try:
            yield
        finally:
            self._pinned.reset(token)
            conn.close()

    def _run(self, fn):
        try:
            pinned = self._pinned.get()
            if pinned is not None:
                return fn(pinned)
            with self.engine.connect() as conn:
                return fn(conn)
        except SQLAlchemyError as e:
            # Keep the driver message out of client responses; callers log it.
            raise DataSourceError(f"database query failed ({type(e).__name__})") from e

    # -------------------------------------------------------------- interface
    def customer_exists(self, customer_id: str) -> bool:
        stmt = select(Customer.customer_id).where(Customer.customer_id == customer_id)
        return self._run(lambda c: c.execute(stmt).first() is not None)

    def get_purchase_records(self, customer_id: str) -> list[PurchaseRecord]:
        stmt = (
            select(OrderItem.order_item_id, OrderItem.order_id, Order.customer_id, OrderItem.product_id,
                   Order.order_date, OrderItem.quantity)
            .join(Order, Order.order_id == OrderItem.order_id)
            .where(Order.customer_id == customer_id, Order.source.in_(self.order_sources))
        )
        rows = self._run(lambda c: c.execute(stmt).all())
        return [PurchaseRecord(order_item_id=r.order_item_id, order_id=r.order_id, customer_id=r.customer_id,
                               product_id=r.product_id, order_date=r.order_date, quantity=r.quantity)
                for r in rows]

    def get_products(self, product_ids: Iterable[str]) -> dict[str, ProductInfo]:
        ids = sorted(set(product_ids))
        if not ids:
            return {}
        stmt = select(Product.product_id, Product.product_name, Product.brand, Product.product_type,
                      Product.category, Product.subcategory, Product.consumption_pattern, Product.pack_size,
                      Product.image_url, Product.replenishable, Product.price,
                      Product.discount_price).where(Product.product_id.in_(ids))
        rows = self._run(lambda c: c.execute(stmt).all())
        return {r.product_id: ProductInfo(**r._asdict()) for r in rows}

    def get_latest_purchase_date(self) -> dt.date | None:
        stmt = select(func.max(Order.order_date)).where(Order.source.in_(self.order_sources))
        return self._run(lambda c: c.execute(stmt).scalar())

    def get_live_subscription_product_ids(self, customer_id: str) -> set[str]:
        def query(conn):
            if self._has_subscriptions is None:
                self._has_subscriptions = inspect(conn).has_table("subscriptions")
            if not self._has_subscriptions:
                return set()
            sql = "SELECT DISTINCT product_id FROM subscriptions WHERE customer_id = :customer_id"
            params: dict = {"customer_id": customer_id}
            stmt = text(sql)
            if self.inactive_subscription_statuses:
                stmt = text(sql + " AND status NOT IN :inactive").bindparams(bindparam("inactive", expanding=True))
                params["inactive"] = list(self.inactive_subscription_statuses)
            return {row[0] for row in conn.execute(stmt, params)}
        return self._run(query)
