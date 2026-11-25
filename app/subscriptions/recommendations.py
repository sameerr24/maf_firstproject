"""Bridge to Sameer's recommendation engine (recommendation_engine) for subscription drafts.

The engine is the only implementation of the scoring; nothing is re-computed here. It runs on
the caller's database engine (a SqlPurchaseRepository over the session's bind), so a request
reads the same database it writes to (and tests stay inside their throwaway schema).

The engine's own rules apply: purchase history is manual orders only, the reference date
comes from scoring_config.json, and products the customer already subscribes to are not
recommended. When a product isn't recommended (too few purchases, not replenishable,
already subscribed) or the engine fails, there is no recommendation and the draft falls back
to the defaults in app.constants; a recommendation must never break subscribing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app import constants as C
from recommendation_engine import RecommendationEngine, RecommendationError, load_config
from recommendation_engine.data.sql_repository import SqlPurchaseRepository

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class EngineRecommendation:
    """What the subscription module uses from one engine recommendation."""
    quantity: int
    frequency_days: int
    rank: int
    score: float
    status: str
    reason: str


@lru_cache(maxsize=1)
def _config():
    return load_config()


def _engine_for(session: Session) -> RecommendationEngine:
    config = _config()
    repo = SqlPurchaseRepository(session.get_bind(), order_sources=config.history.order_sources,
                                 inactive_subscription_statuses=config.eligibility.inactive_subscription_statuses)
    return RecommendationEngine(repository=repo, config=config)


def recommend(session: Session, customer_id: str, product_id: str) -> EngineRecommendation | None:
    """The engine's recommendation for this customer and product, or None."""
    try:
        engine = _engine_for(session)
        result = engine.generate_recommendations(customer_id=customer_id, limit=engine.config.limits.max_limit)
    except (RecommendationError, SQLAlchemyError):
        log.warning("Recommendation engine unavailable for %s / %s; using defaults", customer_id, product_id,
                    exc_info=True)
        return None
    rec = next((r for r in result.recommendations if r.product_id == product_id), None)
    if rec is None:
        return None
    quantity = rec.suggested_quantity or C.DEFAULT_QUANTITY
    frequency = (max(1, round(rec.expected_purchase_interval)) if rec.expected_purchase_interval is not None
                 else C.DEFAULT_FREQUENCY_DAYS)
    return EngineRecommendation(
        quantity=min(max(quantity, C.QUANTITY_MIN), C.QUANTITY_MAX),
        frequency_days=min(max(frequency, C.FREQUENCY_DAYS_MIN), C.FREQUENCY_DAYS_MAX),
        rank=rec.rank, score=round(rec.final_score, 4), status=rec.replenishment_status, reason=rec.explanation)
