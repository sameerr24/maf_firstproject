"""Days between consecutive purchase occasions of one product."""

from __future__ import annotations

from typing import Sequence

from recommendation_engine.data.purchase_data import PurchaseOccasion


def compute_purchase_intervals(occasions: Sequence[PurchaseOccasion]) -> tuple[list[int], int]:
    """Return ``(intervals, dropped)``.

    Occasions are sorted by date first. ``intervals[i]`` is the number of days from
    occasion ``i`` to occasion ``i + 1``, oldest first. Zero or negative gaps (possible only
    if same-day merging is switched off or the input is inconsistent) are dropped rather
    than kept as artificial intervals; ``dropped`` counts them. Fewer than two occasions
    give no intervals.
    """
    dates = sorted(o.occasion_date for o in occasions)
    intervals, dropped = [], 0
    for previous, current in zip(dates, dates[1:]):
        gap = (current - previous).days
        if gap > 0:
            intervals.append(gap)
        else:
            dropped += 1
    return intervals, dropped
