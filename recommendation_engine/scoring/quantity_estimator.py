"""Suggested reorder quantity (never used for ranking).

Median of the units bought per purchase occasion (lines of one order are already added
together, so duplicate lines cannot inflate it), rounded half up and never below
``quantity.min_quantity``. Units are whatever ``order_items.quantity`` counts: packs of the
product's ``pack_size`` (e.g. 2 x "500 ml"). Occasions without a valid quantity are
ignored; if none has one, no quantity is suggested.

Examples: 1, 1, 2, 1, 1 -> 1    5, 5, 10, 5 -> 5    1, 2 -> 1.5 -> 2
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from typing import Sequence

from recommendation_engine.config import QuantitySettings
from recommendation_engine.data.purchase_data import PurchaseOccasion


@dataclass(frozen=True)
class QuantityEstimate:
    suggested_quantity: int | None
    median_quantity: float | None
    occasions_used: int


def round_half_up(x: float) -> int:
    return int(math.floor(x + 0.5))


def estimate_quantity(occasions: Sequence[PurchaseOccasion], settings: QuantitySettings) -> QuantityEstimate:
    quantities = [o.quantity for o in occasions if isinstance(o.quantity, int) and o.quantity > 0]
    if not quantities:
        return QuantityEstimate(None, None, 0)
    median = float(statistics.median(quantities))
    return QuantityEstimate(max(settings.min_quantity, round_half_up(median)), median, len(quantities))
