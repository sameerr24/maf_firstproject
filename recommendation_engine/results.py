"""Result objects returned by ``RecommendationEngine``."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any


@dataclass(frozen=True)
class Recommendation:
    """Everything calculated for one recommended product. Scores are unrounded."""
    product_id: str
    product_name: str
    brand: str | None
    product_type: str | None
    category: str | None
    habit_category: str | None
    image_url: str | None
    pack_size: str | None
    price: Decimal | None
    discount_price: Decimal | None
    purchase_count: int
    first_purchase_date: dt.date
    last_purchase_date: dt.date
    days_since_last_purchase: int | None
    expected_purchase_interval: float | None
    due_ratio: float | None
    due_score: float | None
    habit_score: float
    habit_threshold: float
    consistency_score: float | None
    consistency_status: str
    coefficient_of_variation: float | None
    final_score: float
    lapse_factor: float
    fallbacks_applied: tuple[str, ...]
    replenishment_status: str
    suggested_quantity: int | None
    rank: int = 0
    explanation: str = ""


@dataclass(frozen=True)
class RecommendationResult:
    customer_id: str
    reference_date: dt.date
    limit: int
    total_eligible_products: int
    recommendations: list[Recommendation]
    excluded_products: dict[str, int] = field(default_factory=dict)
    data_quality: dict[str, Any] = field(default_factory=dict)

    @property
    def total_recommendations(self) -> int:
        return len(self.recommendations)
