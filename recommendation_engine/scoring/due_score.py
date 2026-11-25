"""Due Score: how close the customer is to their expected replenishment date.

    DueRatio = DaysSinceLastPurchase / ExpectedPurchaseInterval
    DueScore = min(1, max(0, DueRatio))

Returns ``due_score=None`` (not 0) when it cannot be measured: no expected interval
(fewer than two purchase occasions), a non-positive interval, or a missing/future last
purchase date. The final-score step then applies the configured fallback, and the
caller can still see that the score was not measured.
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass


@dataclass(frozen=True)
class DueAssessment:
    days_since_last_purchase: int | None
    expected_interval: float | None
    due_ratio: float | None   # unbounded: 2.0 means twice the usual gap has passed
    due_score: float | None   # bounded to [0, 1]; None = not measurable

    @property
    def measured(self) -> bool:
        return self.due_score is not None


def compute_due_score(last_purchase_date: dt.date | None, reference_date: dt.date,
                      expected_interval: float | None) -> DueAssessment:
    days_since = None
    if isinstance(last_purchase_date, dt.date):
        days_since = (reference_date - last_purchase_date).days
        if days_since < 0:  # purchase after the reference date: cannot be "since"
            days_since = None

    valid_interval = (expected_interval is not None and isinstance(expected_interval, (int, float))
                      and math.isfinite(expected_interval) and expected_interval > 0)
    if days_since is None or not valid_interval:
        return DueAssessment(days_since, expected_interval if valid_interval else None, None, None)

    ratio = days_since / float(expected_interval)
    return DueAssessment(days_since, float(expected_interval), ratio, min(1.0, max(0.0, ratio)))
