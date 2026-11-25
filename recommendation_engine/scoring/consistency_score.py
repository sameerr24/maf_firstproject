"""Consistency Score: how regular the gaps between purchases are.

    CV               = StandardDeviation(intervals) / Mean(intervals)
    ConsistencyScore = 1 / (1 + CV)

Standard deviation: the SAMPLE standard deviation (ddof=1) by default
(``consistency.std_ddof``). With the handful of intervals a customer typically has, the
population formula (ddof=0) understates spread and would make short histories look
more regular than they are.

History requirement: at least ``consistency.min_intervals`` intervals (default 2, i.e. 3
purchase occasions). With fewer, the score is NOT reported as 1; it is ``None`` with status
``insufficient_history``, and the final score uses the configured neutral fallback.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np

from recommendation_engine.config import ConsistencySettings

SUFFICIENT_HISTORY = "sufficient_history"
INSUFFICIENT_HISTORY = "insufficient_history"


@dataclass(frozen=True)
class ConsistencyAssessment:
    consistency_score: float | None  # None = not measurable (never a disguised fallback)
    coefficient_of_variation: float | None
    status: str
    interval_count: int


def compute_consistency_score(intervals: Sequence[float], settings: ConsistencySettings) -> ConsistencyAssessment:
    values = np.asarray([x for x in intervals if x is not None], dtype=float)
    values = values[np.isfinite(values) & (values > 0)]
    n = int(values.size)
    if n < max(settings.min_intervals, settings.std_ddof + 1):
        return ConsistencyAssessment(None, None, INSUFFICIENT_HISTORY, n)
    mean = float(values.mean())
    cv = float(values.std(ddof=settings.std_ddof)) / mean
    return ConsistencyAssessment(1.0 / (1.0 + cv), cv, SUFFICIENT_HISTORY, n)
