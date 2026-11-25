"""Personalized replenishment recommendations, independent of any web framework.

    from recommendation_engine import RecommendationEngine
    RecommendationEngine().generate_recommendations(customer_id="CUST-0001", limit=10)

See ``engine.py`` for the pipeline and ``config/scoring_config.json`` for the tunable values.
"""

from recommendation_engine.config import ScoringConfig, load_config  # noqa: F401
from recommendation_engine.data import (  # noqa: F401
    InMemoryPurchaseRepository, ProductInfo, PurchaseRecord, PurchaseRepository,
)
from recommendation_engine.engine import RecommendationEngine  # noqa: F401
from recommendation_engine.errors import (  # noqa: F401
    ConfigError, CustomerNotFoundError, DataSourceError, InvalidRequestError, RecommendationError,
)
from recommendation_engine.results import Recommendation, RecommendationResult  # noqa: F401
