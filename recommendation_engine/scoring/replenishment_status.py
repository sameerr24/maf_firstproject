"""Replenishment status from the Due Score (thresholds come from the config).

    DueScore <  due_soon_min                      -> Not Due Yet
    due_soon_min <= DueScore < approaching_due_min -> Due Soon
    approaching_due_min <= DueScore < 1            -> Approaching Due
    DueScore == 1 and DueRatio <= overdue_ratio    -> Due
    DueScore == 1 and DueRatio >  overdue_ratio    -> Overdue
    past the lapse threshold (see lapse.py)        -> Lapsed
    Due Score not measurable                       -> Insufficient History

The status only labels a product; it never removes it from the recommendations.
"""

from __future__ import annotations

from enum import Enum

from recommendation_engine.config import ReplenishmentStatusSettings
from recommendation_engine.scoring.due_score import DueAssessment


class ReplenishmentStatus(str, Enum):
    NOT_DUE_YET = "Not Due Yet"
    DUE_SOON = "Due Soon"
    APPROACHING_DUE = "Approaching Due"
    DUE = "Due"
    OVERDUE = "Overdue"
    LAPSED = "Lapsed"
    INSUFFICIENT_HISTORY = "Insufficient History"


def classify_replenishment(due: DueAssessment, settings: ReplenishmentStatusSettings,
                           lapsed: bool = False) -> ReplenishmentStatus:
    if due.due_score is None or due.due_ratio is None:
        return ReplenishmentStatus.INSUFFICIENT_HISTORY
    if lapsed:
        return ReplenishmentStatus.LAPSED
    if due.due_score >= 1.0:
        return ReplenishmentStatus.OVERDUE if due.due_ratio > settings.overdue_ratio else ReplenishmentStatus.DUE
    if due.due_score >= settings.approaching_due_min:
        return ReplenishmentStatus.APPROACHING_DUE
    if due.due_score >= settings.due_soon_min:
        return ReplenishmentStatus.DUE_SOON
    return ReplenishmentStatus.NOT_DUE_YET
