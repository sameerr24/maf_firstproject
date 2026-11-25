"""Plain-language explanations built from a recommendation's own numbers.

Nothing here is product-specific. A sentence is composed from three parts:
  * how established the habit is (Habit Score, purchase count),
  * how regular the purchases are (Consistency Score and its status),
  * timing (days since last purchase vs expected interval, replenishment status).
With too little history, the text says so instead of claiming a pattern.
"""

from __future__ import annotations

import math

from recommendation_engine.config import ExplanationSettings
from recommendation_engine.results import Recommendation
from recommendation_engine.scoring.consistency_score import SUFFICIENT_HISTORY
from recommendation_engine.scoring.replenishment_status import ReplenishmentStatus as S


def _days(n: float) -> str:
    n = int(round(n))
    return "1 day" if n == 1 else f"{n} days"


def _times(n: int) -> str:
    return {1: "once", 2: "twice"}.get(n, f"{n} times")


def _ago(days: int | None) -> str:
    if days is None:
        return "recently"
    return "today" if days == 0 else ("yesterday" if days == 1 else f"{days} days ago")


def _habit_phrase(rec: Recommendation, x: ExplanationSettings) -> str:
    n = rec.purchase_count
    if rec.habit_score >= x.strong_habit_min:
        return f"You buy this regularly ({_times(n)} so far)"
    if rec.habit_score >= x.moderate_habit_min:
        return f"You buy this fairly often ({_times(n)} so far)"
    return f"You have bought this {_times(n)}"


def _regularity_phrase(rec: Recommendation, x: ExplanationSettings) -> str:
    if rec.consistency_status != SUFFICIENT_HISTORY or rec.consistency_score is None:
        return ""
    if rec.consistency_score >= x.very_regular_min:
        return "at very regular intervals"
    if rec.consistency_score >= x.fairly_regular_min:
        return "at fairly regular intervals"
    return "at irregular intervals, so the timing is a rough estimate"


def _timing_sentence(rec: Recommendation, x: ExplanationSettings) -> str:
    interval = rec.expected_purchase_interval
    since = rec.days_since_last_purchase
    cadence = f"You usually reorder about every {_days(interval)} and last bought it {_ago(since)}"
    remaining = None if since is None or interval is None else interval - since
    status = rec.replenishment_status
    if status == S.OVERDUE.value:
        return f"{cadence}, so it is overdue for a reorder."
    if status == S.DUE.value:
        return f"{cadence}, so it is due for a reorder now."
    if status == S.APPROACHING_DUE.value:
        return f"{cadence}, so you will likely need it within about {_days(max(1, math.ceil(remaining)))}."
    if status == S.DUE_SOON.value:
        return f"{cadence}, so you may need it in about {_days(max(1, math.ceil(remaining)))}."
    return f"{cadence}, so you probably will not need it for about {_days(max(1, math.ceil(remaining)))}."


def generate_explanation(rec: Recommendation, settings: ExplanationSettings) -> str:
    if rec.replenishment_status == S.INSUFFICIENT_HISTORY.value or rec.expected_purchase_interval is None:
        return (f"You have bought this {_times(rec.purchase_count)}, last {_ago(rec.days_since_last_purchase)}. "
                "There is not enough purchase history yet to estimate when you will need it again.")
    if rec.replenishment_status == S.LAPSED.value:
        return (f"You used to buy this about every {_days(rec.expected_purchase_interval)} "
                f"({_times(rec.purchase_count)} in total), but your last purchase was "
                f"{_ago(rec.days_since_last_purchase)}. It may no longer be part of your routine, "
                "so it is ranked lower until you buy it again.")
    first = _habit_phrase(rec, settings)
    regularity = _regularity_phrase(rec, settings)
    if regularity:
        first = f"{first}, {regularity}"
    elif rec.consistency_status != SUFFICIENT_HISTORY:
        first = f"{first}; with only a few purchases, the timing is an early estimate"
    return f"{first}. {_timing_sentence(rec, settings)}"
