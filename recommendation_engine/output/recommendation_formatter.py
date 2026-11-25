"""Convert results to JSON-ready dicts (used by the API) and to a text table (used by the CLI).

Both share one rounding rule (``SCORE_DECIMALS``) so the CLI and the API show the same numbers.
"""

from __future__ import annotations

from typing import Any

from recommendation_engine.results import Recommendation, RecommendationResult

SCORE_DECIMALS = 3
INTERVAL_DECIMALS = 1


def _r(value: float | None, digits: int = SCORE_DECIMALS) -> float | None:
    return None if value is None else round(float(value), digits)


def _money(value) -> float | None:
    return None if value is None else round(float(value), 2)


def recommendation_to_dict(rec: Recommendation) -> dict[str, Any]:
    return {
        "rank": rec.rank,
        "product_id": rec.product_id,
        "product_name": rec.product_name,
        "brand": rec.brand,
        "product_type": rec.product_type,
        "category": rec.category,
        "product_image": rec.image_url,
        "pack_size": rec.pack_size,
        "price": _money(rec.price),
        "discount_price": _money(rec.discount_price),
        "purchase_count": rec.purchase_count,
        "first_purchase_date": rec.first_purchase_date.isoformat(),
        "last_purchase_date": rec.last_purchase_date.isoformat(),
        "days_since_last_purchase": rec.days_since_last_purchase,
        "expected_purchase_interval": _r(rec.expected_purchase_interval, INTERVAL_DECIMALS),
        "due_ratio": _r(rec.due_ratio),
        "due_score": _r(rec.due_score),
        "habit_score": _r(rec.habit_score),
        "habit_category": rec.habit_category,
        "habit_threshold": rec.habit_threshold,
        "consistency_score": _r(rec.consistency_score),
        "consistency_status": rec.consistency_status,
        "final_score": _r(rec.final_score),
        "lapse_factor": _r(rec.lapse_factor),
        "fallbacks_applied": list(rec.fallbacks_applied),
        "replenishment_status": rec.replenishment_status,
        "suggested_quantity": rec.suggested_quantity,
        "explanation": rec.explanation,
    }


def result_to_dict(result: RecommendationResult) -> dict[str, Any]:
    return {
        "customer_id": result.customer_id,
        "reference_date": result.reference_date.isoformat(),
        "total_recommendations": result.total_recommendations,
        "total_eligible_products": result.total_eligible_products,
        "recommendations": [recommendation_to_dict(r) for r in result.recommendations],
    }


def _fmt(value: float | None, digits: int = 2) -> str:
    return "-" if value is None else f"{value:.{digits}f}"


def render_table(result: RecommendationResult, *, explanations: bool = True) -> str:
    """Human-readable output for the terminal."""
    lines = [
        f"Customer {result.customer_id}  |  reference date {result.reference_date.isoformat()}  |  "
        f"{result.total_recommendations} of {result.total_eligible_products} eligible products",
    ]
    if result.excluded_products:
        lines.append("Excluded: " + ", ".join(f"{k}={v}" for k, v in sorted(result.excluded_products.items())))
    if not result.recommendations:
        lines.append("No recommendations: the customer has no eligible purchase history.")
        return "\n".join(lines)
    header = (f"{'#':>2}  {'product':<34} {'buys':>4} {'last':>10} {'since':>5} {'every':>5} "
              f"{'due':>5} {'habit':>5} {'cons':>5} {'final':>5}  {'status':<20} {'qty':>3}")
    lines += ["", header, "-" * len(header)]
    for r in result.recommendations:
        name = f"{r.product_name} ({r.pack_size})" if r.pack_size else r.product_name
        lines.append(
            f"{r.rank:>2}  {name[:34]:<34} {r.purchase_count:>4} {r.last_purchase_date.isoformat():>10} "
            f"{'-' if r.days_since_last_purchase is None else r.days_since_last_purchase:>5} "
            f"{_fmt(r.expected_purchase_interval, 1):>5} {_fmt(r.due_score):>5} {_fmt(r.habit_score):>5} "
            f"{_fmt(r.consistency_score):>5} {_fmt(r.final_score):>5}  {r.replenishment_status:<20} "
            f"{'-' if r.suggested_quantity is None else r.suggested_quantity:>3}")
        if explanations:
            lines.append(f"    {r.explanation}")
    if any(r.fallbacks_applied for r in result.recommendations):
        lines += ["", "'-' = not measurable (too little history); a configured fallback was used in the final score."]
    return "\n".join(lines)
