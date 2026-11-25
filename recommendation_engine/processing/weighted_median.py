"""Weighted median with recency weights.

Methodology
-----------
The expected replenishment interval is the *weighted median* of a customer's past
intervals for a product. A median ignores the odd long gap (a vacation, a skipped
week) that would drag a mean upwards. Weighting by recency lets the estimate follow
changes in habit (a growing household, a switch to bigger packs).

Recency weight of interval ``i`` (0 = oldest, n-1 = newest)::

    age_i    = (n - 1) - i
    weight_i = 0.5 ** (age_i / half_life)

so the newest interval has weight 1, an interval ``half_life`` positions older has 0.5,
and so on. ``half_life=None`` gives equal weights (an ordinary median).

Weighted median definition: sort the values; walk the cumulative weight; return the
first value whose cumulative weight reaches half the total. If the cumulative weight
lands exactly on one half, return the average of that value and the next one (so equal
weights reproduce the ordinary median, including for even counts).
"""

from __future__ import annotations

import math
from typing import Sequence

import numpy as np


def recency_weights(n: int, half_life: float | None) -> list[float]:
    """Weights for ``n`` values ordered oldest -> newest."""
    if n < 0:
        raise ValueError("n must be >= 0")
    if half_life is None:
        return [1.0] * n
    if half_life <= 0:
        raise ValueError("half_life must be > 0")
    return [0.5 ** (((n - 1) - i) / half_life) for i in range(n)]


def weighted_median(values: Sequence[float], weights: Sequence[float]) -> float | None:
    """Weighted median of ``values``; None for empty input. Weights must be > 0 and finite."""
    if len(values) != len(weights):
        raise ValueError("values and weights must have the same length")
    if not values:
        return None
    v = np.asarray(values, dtype=float)
    w = np.asarray(weights, dtype=float)
    if not (np.all(np.isfinite(v)) and np.all(np.isfinite(w))):
        raise ValueError("values and weights must be finite")
    if np.any(w <= 0):
        raise ValueError("weights must be > 0")

    order = np.argsort(v, kind="stable")
    v, w = v[order], w[order]
    cumulative = np.cumsum(w)
    half = cumulative[-1] / 2.0
    tolerance = 1e-12 * cumulative[-1]  # guard against float rounding just below one half
    idx = int(np.searchsorted(cumulative, half - tolerance, side="left"))
    if math.isclose(cumulative[idx], half, rel_tol=0, abs_tol=tolerance) and idx + 1 < len(v):
        return float((v[idx] + v[idx + 1]) / 2.0)
    return float(v[idx])


def recency_weighted_median(intervals: Sequence[float], half_life: float | None,
                            max_intervals: int | None = None) -> float | None:
    """Expected interval from intervals ordered oldest -> newest."""
    values = list(intervals)[-max_intervals:] if max_intervals else list(intervals)
    return weighted_median(values, recency_weights(len(values), half_life))
