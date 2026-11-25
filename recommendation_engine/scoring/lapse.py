"""Lapse factor: lowers the ranking of habits the customer has abandoned.

The Due Score is capped at 1 (spec), so "2 days late" and "2 months late" look
identical to it. A product bought every 2 days but not for 40 days is more likely a
habit that ended (switched store, stopped using it) than an urgent need. Without a
correction, a lapsed customer's list would be all "Overdue" at the top.

    lapse_start  = max(start_ratio * ExpectedInterval, ExpectedInterval + min_extra_days)
    lapsed       = DaysSinceLastPurchase > lapse_start
    excess       = (DaysSinceLastPurchase - lapse_start) / ExpectedInterval   (in intervals)
    lapse_factor = max(floor, 0.5 ** (excess / half_life_intervals))          (1.0 if not lapsed)

    FinalScore   = (w_due*Due + w_habit*Habit + w_consistency*Consistency) * lapse_factor

* ``min_extra_days`` keeps fast products from lapsing after a short holiday:
  milk every 2 days lapses after 9 days, not 4.
* The factor decays smoothly (no cliff) and never goes below ``floor``, so lapsed
  products still order among themselves by habit and consistency.
* The Due Score itself is unchanged and still reported as the spec defines it.
* No expected interval (too little history) means no lapse judgement: factor 1.
"""

from __future__ import annotations

from dataclasses import dataclass

from recommendation_engine.config import LapseSettings
from recommendation_engine.scoring.due_score import DueAssessment


@dataclass(frozen=True)
class LapseAssessment:
    lapse_factor: float            # 1.0 = no penalty
    lapsed: bool
    lapse_start_days: float | None  # days since last purchase after which the habit counts as lapsed


NO_LAPSE = LapseAssessment(1.0, False, None)


def compute_lapse(due: DueAssessment, settings: LapseSettings) -> LapseAssessment:
    if not settings.enabled or due.due_score is None or due.expected_interval is None \
            or due.days_since_last_purchase is None:
        return NO_LAPSE
    expected = due.expected_interval
    start = max(settings.start_ratio * expected, expected + settings.min_extra_days)
    days = due.days_since_last_purchase
    if days <= start:
        return LapseAssessment(1.0, False, start)
    excess_intervals = (days - start) / expected
    factor = max(settings.floor, 0.5 ** (excess_intervals / settings.half_life_intervals))
    return LapseAssessment(factor, True, start)
