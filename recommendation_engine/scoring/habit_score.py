"""Habit Score: how established the purchasing habit is.

    HabitScore = min(1, PurchaseOccasions / CategoryHabitThreshold)

``PurchaseOccasions`` counts distinct occasions (never order lines). The threshold is
looked up from the product's category value (the catalogue field named in
``habit.category_field``); unknown or missing categories use ``habit.default_threshold``.
"""

from __future__ import annotations

from dataclasses import dataclass

from recommendation_engine.config import HabitSettings


@dataclass(frozen=True)
class HabitAssessment:
    habit_score: float
    purchase_occasions: int
    threshold: float
    category: str | None
    threshold_source: str  # "category" | "default"


def compute_habit_score(purchase_occasions: int, category: str | None, settings: HabitSettings) -> HabitAssessment:
    count = max(0, int(purchase_occasions))
    configured = category is not None and str(category).strip() in settings.thresholds
    threshold = settings.threshold_for(category)
    return HabitAssessment(habit_score=min(1.0, count / threshold), purchase_occasions=count, threshold=threshold,
                           category=category, threshold_source="category" if configured else "default")
