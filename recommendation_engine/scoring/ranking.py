"""Final score and ranking.

    FinalScore = (w_due * DueScore + w_habit * HabitScore + w_consistency * ConsistencyScore) * LapseFactor

Weights come from the config and sum to 1, every input is in [0, 1] and the lapse
factor is in [floor, 1] (1 unless the habit has lapsed; see lapse.py), so the final
score is in [0, 1]. When Due or Consistency could not be measured, the configured
fallback value is used inside this formula only, and its name is recorded in
``fallbacks_applied`` so it is never mistaken for a measured score. Quantity plays no part.

Ranking: all eligible products are scored first, then sorted by final score (highest
first) with ``product_id`` as a deterministic tie-breaker, and only then cut to the limit.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence, TypeVar

from recommendation_engine.config import FallbackScores, ScoreWeights

T = TypeVar("T")


@dataclass(frozen=True)
class FinalScore:
    final_score: float      # base_score * lapse_factor
    base_score: float       # the weighted sum
    due_component: float
    habit_component: float
    consistency_component: float
    lapse_factor: float
    fallbacks_applied: tuple[str, ...]


def compute_final_score(due_score: float | None, habit_score: float, consistency_score: float | None,
                        weights: ScoreWeights, fallbacks: FallbackScores, lapse_factor: float = 1.0) -> FinalScore:
    """Weighted sum of the three scores, multiplied by the lapse factor (1.0 = no lapse; see lapse.py)."""
    applied = []
    if due_score is None:
        due_score = fallbacks.due_score
        applied.append("due_score")
    if consistency_score is None:
        consistency_score = fallbacks.consistency_score
        applied.append("consistency_score")
    due_c = weights.due * due_score
    habit_c = weights.habit * habit_score
    cons_c = weights.consistency * consistency_score
    base = min(1.0, max(0.0, due_c + habit_c + cons_c))
    factor = min(1.0, max(0.0, lapse_factor))
    return FinalScore(base * factor, base, due_c, habit_c, cons_c, factor, tuple(applied))


def rank_candidates(candidates: Sequence[T], *, score: Callable[[T], float], product_id: Callable[[T], str],
                    limit: int | None = None) -> list[tuple[int, T]]:
    """Return ``[(rank, candidate), ...]`` for the top ``limit`` (all if None), rank 1 = best."""
    ordered = sorted(candidates, key=lambda c: (-round(score(c), 12), product_id(c)))
    if limit is not None:
        ordered = ordered[:limit]
    return [(i, c) for i, c in enumerate(ordered, start=1)]
