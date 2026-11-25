"""RecommendationEngine: the single implementation of the recommendation algorithm.

Used unchanged by ``scripts/run_recommendations.py`` (terminal) and by the FastAPI
service (``recommendation_api``). It knows nothing about HTTP, Flask or FastAPI; it reads
data only through an injected ``PurchaseRepository``.

    from recommendation_engine import RecommendationEngine
    result = RecommendationEngine().generate_recommendations(customer_id="CUST-0001", limit=10)

Pipeline per customer:
  1. purchase lines -> purchase occasions (one per order and product, same-day merged)
  2. eligibility: in the catalogue, replenishable, not already subscribed, enough occasions
  3. per product: intervals -> recency-weighted median expected interval
     -> Due / Habit / Consistency scores -> lapse factor -> Final score, status,
     suggested quantity
  4. rank ALL eligible products (final score desc, product_id asc), then cut to ``limit``
  5. explanation for each returned product
"""

from __future__ import annotations

import datetime as dt
import logging
from collections import Counter
from contextlib import nullcontext
from dataclasses import replace

from recommendation_engine.config import ScoringConfig, load_config
from recommendation_engine.data.purchase_data import ProductInfo, PurchaseOccasion
from recommendation_engine.data.repository import PurchaseRepository
from recommendation_engine.errors import CustomerNotFoundError, InvalidRequestError
from recommendation_engine.output.explanation_generator import generate_explanation
from recommendation_engine.processing.purchase_intervals import compute_purchase_intervals
from recommendation_engine.processing.purchase_occasions import DataQualityReport, build_purchase_occasions
from recommendation_engine.processing.weighted_median import recency_weighted_median
from recommendation_engine.results import Recommendation, RecommendationResult
from recommendation_engine.scoring.consistency_score import compute_consistency_score
from recommendation_engine.scoring.due_score import compute_due_score
from recommendation_engine.scoring.habit_score import compute_habit_score
from recommendation_engine.scoring.lapse import compute_lapse
from recommendation_engine.scoring.quantity_estimator import estimate_quantity
from recommendation_engine.scoring.ranking import compute_final_score, rank_candidates
from recommendation_engine.scoring.replenishment_status import classify_replenishment

log = logging.getLogger(__name__)

MAX_CUSTOMER_ID_LENGTH = 64


class RecommendationEngine:
    def __init__(self, repository: PurchaseRepository | None = None, config: ScoringConfig | None = None):
        """``repository`` defaults to ``SqlPurchaseRepository`` on DATABASE_URL, created on first use."""
        self.config = config if config is not None else load_config()
        self._repository = repository

    @property
    def repository(self) -> PurchaseRepository:
        if self._repository is None:
            from recommendation_engine.data.sql_repository import SqlPurchaseRepository
            self._repository = SqlPurchaseRepository(
                order_sources=self.config.history.order_sources,
                inactive_subscription_statuses=self.config.eligibility.inactive_subscription_statuses)
        return self._repository

    # ------------------------------------------------------------------ public API
    def generate_recommendations(self, customer_id: str, limit: int | None = None,
                                 as_of: dt.date | str | None = None) -> RecommendationResult:
        """Top-``limit`` replenishment recommendations for one customer.

        ``as_of`` evaluates "today" as a given date (purchases after it are ignored);
        by default the reference date comes from ``history.reference_date`` in the config.
        Raises ``InvalidRequestError``, ``CustomerNotFoundError`` or ``DataSourceError``.
        """
        customer_id = self._validate_customer_id(customer_id)
        limit = self._validate_limit(limit)
        as_of_date = self._validate_as_of(as_of)

        repo = self.repository
        session = getattr(repo, "session", None)  # optional hook, e.g. one DB connection per request
        with session() if callable(session) else nullcontext():
            if not repo.customer_exists(customer_id):
                raise CustomerNotFoundError(customer_id)
            reference_date = as_of_date or self._default_reference_date(repo)
            records = repo.get_purchase_records(customer_id)
            report = DataQualityReport()
            occasions = build_purchase_occasions(records, customer_id, reference_date,
                                                 merge_same_day=self.config.history.merge_same_day_occasions,
                                                 report=report)
            products = repo.get_products(occasions.keys()) if occasions else {}
            subscribed = (repo.get_live_subscription_product_ids(customer_id)
                          if occasions and self.config.eligibility.exclude_live_subscriptions else set())

        excluded: Counter[str] = Counter()
        candidates: list[Recommendation] = []
        for product_id, product_occasions in occasions.items():
            reason = self._exclusion_reason(product_id, product_occasions, products, subscribed)
            if reason:
                excluded[reason] += 1
                continue
            candidates.append(self._score_product(products[product_id], product_occasions, reference_date, report))

        ranked = rank_candidates(candidates, score=lambda r: r.final_score, product_id=lambda r: r.product_id,
                                 limit=limit)
        recommendations = []
        for rank, rec in ranked:
            rec = replace(rec, rank=rank)
            recommendations.append(replace(rec, explanation=generate_explanation(rec, self.config.explanations)))

        quality = report.as_dict()
        if any(k != "records_in" for k in quality):
            log.info("data quality issues for customer %s: %s", customer_id, quality)
        return RecommendationResult(customer_id=customer_id, reference_date=reference_date, limit=limit,
                                    total_eligible_products=len(candidates), recommendations=recommendations,
                                    excluded_products=dict(excluded), data_quality=quality)

    # ------------------------------------------------------------------ steps
    def _exclusion_reason(self, product_id: str, occasions: list[PurchaseOccasion],
                          products: dict[str, ProductInfo], subscribed: set[str]) -> str | None:
        product = products.get(product_id)
        if product is None:
            return "not_in_catalogue"
        if self.config.eligibility.only_replenishable and not product.replenishable:
            return "not_replenishable"
        if product_id in subscribed:
            return "already_subscribed"
        if len(occasions) < self.config.eligibility.min_purchase_occasions:
            return "too_few_purchases"
        return None

    def _score_product(self, product: ProductInfo, occasions: list[PurchaseOccasion], reference_date: dt.date,
                       report: DataQualityReport) -> Recommendation:
        cfg = self.config
        intervals, dropped = compute_purchase_intervals(occasions)
        report.non_positive_intervals_dropped += dropped
        expected = recency_weighted_median(intervals, cfg.weighted_median.half_life_intervals,
                                           cfg.weighted_median.max_intervals)
        last_date = occasions[-1].occasion_date

        due = compute_due_score(last_date, reference_date, expected)
        habit_category = product.attribute(cfg.habit.category_field)
        habit = compute_habit_score(len(occasions), habit_category, cfg.habit)
        consistency = compute_consistency_score(intervals, cfg.consistency)
        lapse = compute_lapse(due, cfg.lapse)
        final = compute_final_score(due.due_score, habit.habit_score, consistency.consistency_score,
                                    cfg.weights, cfg.fallbacks, lapse_factor=lapse.lapse_factor)
        status = classify_replenishment(due, cfg.replenishment_status, lapsed=lapse.lapsed)
        quantity = estimate_quantity(occasions, cfg.quantity)

        return Recommendation(
            product_id=product.product_id, product_name=product.product_name, brand=product.brand,
            product_type=product.product_type, category=product.category, habit_category=habit_category,
            image_url=product.image_url, pack_size=product.pack_size,
            price=product.price, discount_price=product.discount_price,
            purchase_count=len(occasions), first_purchase_date=occasions[0].occasion_date,
            last_purchase_date=last_date, days_since_last_purchase=due.days_since_last_purchase,
            expected_purchase_interval=due.expected_interval, due_ratio=due.due_ratio, due_score=due.due_score,
            habit_score=habit.habit_score, habit_threshold=habit.threshold,
            consistency_score=consistency.consistency_score, consistency_status=consistency.status,
            coefficient_of_variation=consistency.coefficient_of_variation,
            final_score=final.final_score, lapse_factor=final.lapse_factor,
            fallbacks_applied=final.fallbacks_applied,
            replenishment_status=status.value, suggested_quantity=quantity.suggested_quantity,
        )

    def _default_reference_date(self, repo: PurchaseRepository) -> dt.date:
        history = self.config.history
        fixed = history.fixed_reference_date()
        if fixed is not None:
            return fixed
        if history.reference_date == "latest_order_date":
            latest = repo.get_latest_purchase_date()
            if latest is not None:
                return latest
        return dt.date.today()

    # ------------------------------------------------------------------ validation
    @staticmethod
    def _validate_customer_id(customer_id: str) -> str:
        if not isinstance(customer_id, str) or not customer_id.strip():
            raise InvalidRequestError("customer_id must be a non-empty string")
        customer_id = customer_id.strip()
        if len(customer_id) > MAX_CUSTOMER_ID_LENGTH:
            raise InvalidRequestError(f"customer_id must be at most {MAX_CUSTOMER_ID_LENGTH} characters")
        return customer_id

    def _validate_limit(self, limit: int | None) -> int:
        limits = self.config.limits
        if limit is None:
            return limits.default_limit
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise InvalidRequestError("limit must be a whole number")
        if not 1 <= limit <= limits.max_limit:
            raise InvalidRequestError(f"limit must be between 1 and {limits.max_limit}")
        return limit

    @staticmethod
    def _validate_as_of(as_of: dt.date | str | None) -> dt.date | None:
        if as_of is None or (isinstance(as_of, dt.date) and not isinstance(as_of, dt.datetime)):
            return as_of
        if isinstance(as_of, dt.datetime):
            return as_of.date()
        try:
            return dt.date.fromisoformat(str(as_of).strip())
        except ValueError:
            raise InvalidRequestError("as_of must be a date in YYYY-MM-DD format") from None
