"""The data-access interface the engine depends on, plus an in-memory implementation.

The engine only talks to a ``PurchaseRepository``. ``SqlPurchaseRepository`` (in
``sql_repository.py``) reads PostgreSQL/Supabase; ``InMemoryPurchaseRepository`` holds
plain Python objects for tests, notebooks or offline experiments. Any object with these
methods can be injected.

Optional: a repository may also offer ``session()``, a context manager the engine wraps
around all calls of one request (the SQL repository uses it to reuse one connection).
"""

from __future__ import annotations

import datetime as dt
from typing import Iterable, Protocol, runtime_checkable

from recommendation_engine.data.purchase_data import ProductInfo, PurchaseRecord


@runtime_checkable
class PurchaseRepository(Protocol):
    def customer_exists(self, customer_id: str) -> bool:
        """True if the customer is known."""

    def get_purchase_records(self, customer_id: str) -> list[PurchaseRecord]:
        """Every purchase-history order line of the customer (only orders from the
        configured history sources). Order is not guaranteed."""

    def get_products(self, product_ids: Iterable[str]) -> dict[str, ProductInfo]:
        """Catalogue facts for the given product IDs. Unknown IDs are simply absent."""

    def get_latest_purchase_date(self) -> dt.date | None:
        """Newest order date across all customers in the purchase history (None if empty)."""

    def get_live_subscription_product_ids(self, customer_id: str) -> set[str]:
        """Products the customer currently subscribes to (empty if subscriptions don't exist)."""


class InMemoryPurchaseRepository:
    """A ``PurchaseRepository`` backed by Python lists. No database needed."""

    def __init__(self, customers: Iterable[str], records: Iterable[PurchaseRecord],
                 products: Iterable[ProductInfo], live_subscriptions: dict[str, set[str]] | None = None):
        self._customers = set(customers)
        self._records = list(records)
        self._products = {p.product_id: p for p in products}
        self._subscriptions = {k: set(v) for k, v in (live_subscriptions or {}).items()}

    def customer_exists(self, customer_id: str) -> bool:
        return customer_id in self._customers

    def get_purchase_records(self, customer_id: str) -> list[PurchaseRecord]:
        return [r for r in self._records if r.customer_id == customer_id]

    def get_products(self, product_ids: Iterable[str]) -> dict[str, ProductInfo]:
        return {pid: self._products[pid] for pid in set(product_ids) if pid in self._products}

    def get_latest_purchase_date(self) -> dt.date | None:
        dates = [r.order_date for r in self._records if isinstance(r.order_date, dt.date)]
        return max(dates) if dates else None

    def get_live_subscription_product_ids(self, customer_id: str) -> set[str]:
        return set(self._subscriptions.get(customer_id, set()))
