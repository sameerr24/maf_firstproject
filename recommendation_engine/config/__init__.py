"""Load and validate ``scoring_config.json``.

All tunable numbers live in the JSON file. This module turns it into typed, immutable
settings and rejects inconsistent values (weights that don't sum to 1, thresholds out of
order, ...) as soon as the config is loaded, not halfway through a request.
"""

from __future__ import annotations

import datetime as dt
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from recommendation_engine.errors import ConfigError

DEFAULT_CONFIG_PATH = Path(__file__).with_name("scoring_config.json")
REFERENCE_DATE_MODES = ("latest_order_date", "today")


@dataclass(frozen=True)
class HistorySettings:
    order_sources: tuple[str, ...]
    reference_date: str  # "latest_order_date" | "today" | ISO date
    merge_same_day_occasions: bool

    def fixed_reference_date(self) -> dt.date | None:
        return None if self.reference_date in REFERENCE_DATE_MODES else dt.date.fromisoformat(self.reference_date)


@dataclass(frozen=True)
class EligibilitySettings:
    only_replenishable: bool
    min_purchase_occasions: int
    exclude_live_subscriptions: bool
    inactive_subscription_statuses: tuple[str, ...]


@dataclass(frozen=True)
class WeightedMedianSettings:
    half_life_intervals: float | None
    max_intervals: int | None


@dataclass(frozen=True)
class ScoreWeights:
    due: float
    habit: float
    consistency: float


@dataclass(frozen=True)
class FallbackScores:
    due_score: float
    consistency_score: float


@dataclass(frozen=True)
class HabitSettings:
    category_field: str
    thresholds: Mapping[str, float]
    default_threshold: float

    def threshold_for(self, category: str | None) -> float:
        if category is None:
            return self.default_threshold
        return self.thresholds.get(str(category).strip(), self.default_threshold)


@dataclass(frozen=True)
class ConsistencySettings:
    min_intervals: int
    std_ddof: int


@dataclass(frozen=True)
class ReplenishmentStatusSettings:
    due_soon_min: float
    approaching_due_min: float
    overdue_ratio: float


@dataclass(frozen=True)
class LapseSettings:
    enabled: bool
    start_ratio: float
    min_extra_days: float
    half_life_intervals: float
    floor: float


@dataclass(frozen=True)
class QuantitySettings:
    min_quantity: int


@dataclass(frozen=True)
class ExplanationSettings:
    strong_habit_min: float
    moderate_habit_min: float
    very_regular_min: float
    fairly_regular_min: float


@dataclass(frozen=True)
class LimitSettings:
    default_limit: int
    max_limit: int


@dataclass(frozen=True)
class ScoringConfig:
    history: HistorySettings
    eligibility: EligibilitySettings
    weighted_median: WeightedMedianSettings
    weights: ScoreWeights
    fallbacks: FallbackScores
    habit: HabitSettings
    consistency: ConsistencySettings
    replenishment_status: ReplenishmentStatusSettings
    lapse: LapseSettings
    quantity: QuantitySettings
    explanations: ExplanationSettings
    limits: LimitSettings


def _section(raw: Mapping[str, Any], name: str) -> dict[str, Any]:
    value = raw.get(name)
    if not isinstance(value, Mapping):
        raise ConfigError(f"config section '{name}' is missing or not an object")
    return {k: v for k, v in value.items() if not k.startswith("_")}


def _num(section: str, values: Mapping[str, Any], key: str, *, lo: float | None = None,
         hi: float | None = None, integer: bool = False, nullable: bool = False) -> Any:
    if key not in values:
        raise ConfigError(f"config '{section}.{key}' is missing")
    v = values[key]
    if v is None and nullable:
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise ConfigError(f"config '{section}.{key}' must be a number (got {v!r})")
    if integer and int(v) != v:
        raise ConfigError(f"config '{section}.{key}' must be a whole number (got {v!r})")
    if lo is not None and v < lo:
        raise ConfigError(f"config '{section}.{key}' must be >= {lo} (got {v!r})")
    if hi is not None and v > hi:
        raise ConfigError(f"config '{section}.{key}' must be <= {hi} (got {v!r})")
    return int(v) if integer else float(v)


def _bool(section: str, values: Mapping[str, Any], key: str) -> bool:
    v = values.get(key)
    if not isinstance(v, bool):
        raise ConfigError(f"config '{section}.{key}' must be true or false (got {v!r})")
    return v


def _str_list(section: str, values: Mapping[str, Any], key: str, *, allow_empty: bool) -> tuple[str, ...]:
    v = values.get(key)
    if not isinstance(v, list) or not all(isinstance(x, str) and x.strip() for x in v) or (not v and not allow_empty):
        raise ConfigError(f"config '{section}.{key}' must be a list of non-empty strings (got {v!r})")
    return tuple(x.strip() for x in v)


def parse_config(raw: Mapping[str, Any]) -> ScoringConfig:
    """Validate a config dict (same shape as scoring_config.json) and return typed settings."""
    if not isinstance(raw, Mapping):
        raise ConfigError("config must be a JSON object")

    h = _section(raw, "history")
    ref = h.get("reference_date")
    if not isinstance(ref, str):
        raise ConfigError("config 'history.reference_date' must be a string")
    if ref not in REFERENCE_DATE_MODES:
        try:
            dt.date.fromisoformat(ref)
        except ValueError:
            raise ConfigError(f"config 'history.reference_date' must be one of {REFERENCE_DATE_MODES} "
                              f"or an ISO date (got {ref!r})") from None
    history = HistorySettings(
        order_sources=_str_list("history", h, "order_sources", allow_empty=False),
        reference_date=ref,
        merge_same_day_occasions=_bool("history", h, "merge_same_day_occasions"),
    )

    e = _section(raw, "eligibility")
    eligibility = EligibilitySettings(
        only_replenishable=_bool("eligibility", e, "only_replenishable"),
        min_purchase_occasions=_num("eligibility", e, "min_purchase_occasions", lo=1, integer=True),
        exclude_live_subscriptions=_bool("eligibility", e, "exclude_live_subscriptions"),
        inactive_subscription_statuses=_str_list("eligibility", e, "inactive_subscription_statuses", allow_empty=True),
    )

    wm = _section(raw, "weighted_median")
    half_life = _num("weighted_median", wm, "half_life_intervals", nullable=True)
    if half_life is not None and half_life <= 0:
        raise ConfigError("config 'weighted_median.half_life_intervals' must be > 0 or null")
    weighted_median = WeightedMedianSettings(
        half_life_intervals=half_life,
        max_intervals=_num("weighted_median", wm, "max_intervals", lo=1, integer=True, nullable=True),
    )

    w = _section(raw, "weights")
    weights = ScoreWeights(**{k: _num("weights", w, k, lo=0, hi=1) for k in ("due", "habit", "consistency")})
    total = weights.due + weights.habit + weights.consistency
    if not math.isclose(total, 1.0, abs_tol=1e-9):
        raise ConfigError(f"config 'weights' must sum to 1 (got {total:g})")

    f = _section(raw, "fallbacks")
    fallbacks = FallbackScores(due_score=_num("fallbacks", f, "due_score", lo=0, hi=1),
                               consistency_score=_num("fallbacks", f, "consistency_score", lo=0, hi=1))

    hb = _section(raw, "habit")
    field = hb.get("category_field")
    if not isinstance(field, str) or not field.strip():
        raise ConfigError("config 'habit.category_field' must be a non-empty string")
    thresholds_raw = hb.get("thresholds")
    if not isinstance(thresholds_raw, Mapping):
        raise ConfigError("config 'habit.thresholds' must be an object")
    thresholds = {str(k).strip(): _num("habit.thresholds", thresholds_raw, k, lo=1)
                  for k in thresholds_raw if not str(k).startswith("_")}
    habit = HabitSettings(category_field=field.strip(), thresholds=thresholds,
                          default_threshold=_num("habit", hb, "default_threshold", lo=1))

    c = _section(raw, "consistency")
    consistency = ConsistencySettings(min_intervals=_num("consistency", c, "min_intervals", lo=2, integer=True),
                                      std_ddof=_num("consistency", c, "std_ddof", lo=0, hi=1, integer=True))

    r = _section(raw, "replenishment_status")
    status = ReplenishmentStatusSettings(
        due_soon_min=_num("replenishment_status", r, "due_soon_min", lo=0, hi=1),
        approaching_due_min=_num("replenishment_status", r, "approaching_due_min", lo=0, hi=1),
        overdue_ratio=_num("replenishment_status", r, "overdue_ratio", lo=1),
    )
    if not status.due_soon_min < status.approaching_due_min < 1:
        raise ConfigError("config 'replenishment_status' needs due_soon_min < approaching_due_min < 1")

    lp = _section(raw, "lapse")
    lapse = LapseSettings(
        enabled=_bool("lapse", lp, "enabled"),
        start_ratio=_num("lapse", lp, "start_ratio", lo=1),
        min_extra_days=_num("lapse", lp, "min_extra_days", lo=0),
        half_life_intervals=_num("lapse", lp, "half_life_intervals", lo=0.01),
        floor=_num("lapse", lp, "floor", lo=0, hi=1),
    )
    if lapse.start_ratio < status.overdue_ratio:
        raise ConfigError("config 'lapse.start_ratio' must be >= 'replenishment_status.overdue_ratio' "
                          "(a product is overdue before it can be lapsed)")

    q = _section(raw, "quantity")
    quantity = QuantitySettings(min_quantity=_num("quantity", q, "min_quantity", lo=1, integer=True))

    x = _section(raw, "explanations")
    explanations = ExplanationSettings(**{k: _num("explanations", x, k, lo=0) for k in (
        "strong_habit_min", "moderate_habit_min", "very_regular_min", "fairly_regular_min")})

    lim = _section(raw, "limits")
    limits = LimitSettings(default_limit=_num("limits", lim, "default_limit", lo=1, integer=True),
                           max_limit=_num("limits", lim, "max_limit", lo=1, integer=True))
    if limits.default_limit > limits.max_limit:
        raise ConfigError("config 'limits.default_limit' must not exceed 'limits.max_limit'")

    return ScoringConfig(history=history, eligibility=eligibility, weighted_median=weighted_median,
                         weights=weights, fallbacks=fallbacks, habit=habit, consistency=consistency,
                         replenishment_status=status, lapse=lapse, quantity=quantity, explanations=explanations, limits=limits)


def load_config(path: str | Path | None = None) -> ScoringConfig:
    """Read and validate a scoring config file (default: the bundled scoring_config.json)."""
    path = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigError(f"scoring config not found: {path}") from None
    except json.JSONDecodeError as e:
        raise ConfigError(f"scoring config {path.name} is not valid JSON: {e}") from None
    return parse_config(raw)


def load_raw_config(path: str | Path | None = None) -> dict[str, Any]:
    """The config file as a plain dict (handy for tests that tweak one value)."""
    return json.loads(Path(path or DEFAULT_CONFIG_PATH).read_text(encoding="utf-8"))
