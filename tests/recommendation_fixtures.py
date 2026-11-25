"""Helpers for building small in-memory purchase histories in tests (no database)."""

from __future__ import annotations

import datetime as dt
import itertools

from recommendation_engine import InMemoryPurchaseRepository, ProductInfo, PurchaseRecord

REF = dt.date(2026, 11, 15)
_ids = itertools.count(1)


def product(pid: str, pattern: str | None = "daily_perishable", replenishable: bool = True, **kw) -> ProductInfo:
    return ProductInfo(product_id=pid, product_name=kw.pop("name", f"Product {pid}"), consumption_pattern=pattern,
                       replenishable=replenishable, pack_size=kw.pop("pack_size", "1 pc"), **kw)


def purchases(customer: str, pid: str, days_before_ref: list[int], qty: int | list = 1) -> list[PurchaseRecord]:
    """One order per entry, ``days_before_ref`` days before REF."""
    qtys = qty if isinstance(qty, list) else [qty] * len(days_before_ref)
    out = []
    for d, q in zip(days_before_ref, qtys):
        n = next(_ids)
        out.append(PurchaseRecord(order_id=f"ORD-{n}", customer_id=customer, product_id=pid,
                                  order_date=REF - dt.timedelta(days=d), quantity=q, order_item_id=f"OI-{n}"))
    return out


def repo(records, products, customers=("C1",), subscriptions=None) -> InMemoryPurchaseRepository:
    return InMemoryPurchaseRepository(customers=customers, records=records, products=products,
                                      live_subscriptions=subscriptions)
