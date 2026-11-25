"""Service layer: calls the recommendation engine and shapes its result for HTTP.

No scoring happens here; ``RecommendationEngine`` is the only implementation of the
algorithm, shared with ``scripts/run_recommendations.py``.
"""

from __future__ import annotations

import datetime as dt

from recommendation_api.schemas import RecommendationResponse
from recommendation_engine import RecommendationEngine
from recommendation_engine.output import result_to_dict


class RecommendationService:
    def __init__(self, engine: RecommendationEngine):
        self.engine = engine

    @property
    def max_limit(self) -> int:
        return self.engine.config.limits.max_limit

    def customer_exists(self, customer_id: str) -> bool:
        repo = self.engine.repository
        return repo.customer_exists(customer_id)

    def get_recommendations(self, customer_id: str, limit: int | None = None,
                            as_of: dt.date | None = None) -> RecommendationResponse:
        result = self.engine.generate_recommendations(customer_id=customer_id, limit=limit, as_of=as_of)
        return RecommendationResponse.model_validate(result_to_dict(result))
