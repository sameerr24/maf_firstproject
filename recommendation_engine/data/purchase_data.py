"""Plain data types passed between the repository and the engine.

They carry no database or web-framework objects, so the engine can run on data from
PostgreSQL, a CSV file or a hand-written test fixture alike.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from typing import Any


@dataclass(frozen=True)
class PurchaseRecord:
    """One order line as stored (orders JOIN order_items). Values may be dirty; the
    processing layer validates them, so a bad row is skipped rather than crashing."""
    order_id: str
    customer_id: str
    product_id: str
    order_date: dt.date | str | None
    quantity: Any
    order_item_id: str | None = None


@dataclass(frozen=True)
class ProductInfo:
    """Catalogue facts about a product (from the products table)."""
    product_id: str
    product_name: str
    brand: str | None = None
    product_type: str | None = None
    category: str | None = None
    subcategory: str | None = None
    consumption_pattern: str | None = None
    pack_size: str | None = None
    image_url: str | None = None
    replenishable: bool = True
    price: Decimal | None = None           # regular price (products.price)
    discount_price: Decimal | None = None  # subscriber price (products.discount_price)

    def attribute(self, name: str) -> str | None:
        """Value of a catalogue field by name (used for the configurable habit category field)."""
        value = getattr(self, name, None)
        return None if value is None or str(value).strip() == "" else str(value).strip()


@dataclass(frozen=True)
class PurchaseOccasion:
    """One purchase of one product: all lines of that product in one order (and, when
    configured, all orders of that product on the same day) collapsed into a single event."""
    product_id: str
    occasion_date: dt.date
    quantity: int | None  # total valid units bought; None if no line had a usable quantity
    order_ids: tuple[str, ...]
