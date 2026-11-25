"""Recommendation engine unit tests. No database, no web framework."""
import datetime as dt
import subprocess
import sys
from pathlib import Path

import pytest

from recommendation_engine import (
    ConfigError, CustomerNotFoundError, InvalidRequestError, PurchaseRecord, RecommendationEngine, load_config,
)
from recommendation_engine.config import load_raw_config, parse_config
from recommendation_engine.data.purchase_data import PurchaseOccasion
from recommendation_engine.output import result_to_dict
from recommendation_engine.processing import (
    DataQualityReport, build_purchase_occasions, compute_purchase_intervals, recency_weighted_median,
    recency_weights, weighted_median,
)
from recommendation_engine.scoring import (
    INSUFFICIENT_HISTORY, SUFFICIENT_HISTORY, ReplenishmentStatus, classify_replenishment,
    compute_consistency_score, compute_due_score, compute_final_score, compute_habit_score, estimate_quantity,
)
from tests.recommendation_fixtures import REF, product, purchases, repo

ROOT = Path(__file__).resolve().parents[1]
CFG = load_config()


def occ(day: dt.date, qty=1, pid="P1"):
    return PurchaseOccasion(product_id=pid, occasion_date=day, quantity=qty, order_ids=(str(day),))


# ------------------------------------------------------------------ weighted median
@pytest.mark.parametrize("values,weights,expected", [
    ([3, 1, 2], [1, 1, 1], 2),
    ([1, 2, 3, 4], [1, 1, 1, 1], 2.5),          # equal weights reproduce the ordinary median
    ([10, 2, 3], [1, 1, 5], 3),
    ([1, 100], [3, 1], 1),
    ([1, 100], [1, 3], 100),
    ([0.1, 0.2, 0.3, 0.4], [0.1, 0.1, 0.1, 0.1], 0.25),  # float sums landing on exactly one half
    ([7], [0.2], 7),
])
def test_weighted_median(values, weights, expected):
    assert weighted_median(values, weights) == pytest.approx(expected)


def test_weighted_median_edge_cases():
    assert weighted_median([], []) is None
    for bad in ([1, 2], [0, 1]), ([1], [-1]), ([float("nan")], [1]):
        with pytest.raises(ValueError):
            weighted_median(*bad)
    with pytest.raises(ValueError):
        weighted_median([1, 2], [1])


def test_recency_weights_halve_every_half_life():
    w = recency_weights(5, half_life=2)
    assert w[-1] == 1.0 and w[-3] == pytest.approx(0.5) and w[0] == pytest.approx(0.25)
    assert recency_weights(3, None) == [1.0, 1.0, 1.0]


def test_recent_intervals_dominate():
    # habit changed from every 10 days to every 4 days
    intervals = [10, 10, 10, 4, 4]
    assert recency_weighted_median(intervals, half_life=None) == 10
    assert recency_weighted_median(intervals, half_life=1) == 4
    assert recency_weighted_median(intervals, half_life=None, max_intervals=2) == 4


# ------------------------------------------------------------------ purchase occasions
def rec(order, day, qty=1, pid="P1", cust="C1", item=None):
    return PurchaseRecord(order_id=order, customer_id=cust, product_id=pid, order_date=day, quantity=qty,
                          order_item_id=item)


def test_lines_of_one_order_are_one_occasion_and_duplicates_dropped():
    d = dt.date(2026, 11, 1)
    records = [rec("O1", d, 1, item="I1"), rec("O1", d, 2, item="I2"),  # same product twice in one order
               rec("O1", d, 2, item="I2"),                            # exact duplicate row
               rec("O2", d + dt.timedelta(days=3), 1, item="I3")]
    report = DataQualityReport()
    occ_ = build_purchase_occasions(records, "C1", REF, report=report)["P1"]
    assert [(o.occasion_date, o.quantity) for o in occ_] == [(d, 3), (d + dt.timedelta(days=3), 1)]
    assert report.duplicate_records == 1


def test_same_day_orders_merge_and_unmerged_zero_gaps_are_dropped():
    d = dt.date(2026, 11, 1)
    records = [rec("O1", d, 1), rec("O2", d, 2), rec("O3", d + dt.timedelta(days=5), 1)]
    merged = build_purchase_occasions(records, "C1", REF)["P1"]
    assert len(merged) == 2 and merged[0].quantity == 3 and merged[0].order_ids == ("O1", "O2")
    unmerged = build_purchase_occasions(records, "C1", REF, merge_same_day=False)["P1"]
    assert len(unmerged) == 3
    assert compute_purchase_intervals(unmerged) == ([5], 1)  # the 0-day gap is dropped, not kept


def test_dirty_records_are_skipped_safely():
    d = dt.date(2026, 11, 1)
    records = [
        rec("O1", "2026-11-01"),                 # ISO string is fine
        rec("O2", "2026-13-40"),                 # invalid date
        rec("O3", None),                         # missing date
        rec("O4", REF + dt.timedelta(days=1)),   # after the reference date
        rec("O5", d, cust="C2"),                 # another customer
        rec("O6", dt.datetime(2026, 11, 5, 9, 30), qty=0),     # bad quantity: occasion kept
        rec("O7", dt.date(2026, 11, 8), qty="abc"),
        rec("O8", dt.date(2026, 11, 9), qty=2.5),
        rec("O9", dt.date(2026, 11, 10), qty=True),
        rec("", d), rec("O10", d, pid=""),         # missing ids
    ]
    report = DataQualityReport()
    occ_ = build_purchase_occasions(records, "C1", REF, report=report)["P1"]
    assert [o.occasion_date.day for o in occ_] == [1, 5, 8, 9, 10]
    assert [o.quantity for o in occ_] == [1, None, None, None, None]
    assert (report.invalid_date, report.after_reference_date, report.other_customer,
            report.invalid_quantity, report.missing_product) == (2, 1, 1, 4, 2)
    assert build_purchase_occasions([], "C1", REF) == {}


# ------------------------------------------------------------------ intervals
def test_intervals_example_from_spec():
    days = [dt.date(2026, 1, 1), dt.date(2026, 1, 20), dt.date(2026, 1, 10), dt.date(2026, 2, 1)]  # unsorted
    assert compute_purchase_intervals([occ(d) for d in days]) == ([9, 10, 12], 0)
    assert compute_purchase_intervals([occ(days[0])]) == ([], 0)
    assert compute_purchase_intervals([]) == ([], 0)


# ------------------------------------------------------------------ due score + status
@pytest.mark.parametrize("elapsed,expected", [(1, 0.33), (2, 0.67), (3, 1.0), (5, 1.0), (0, 0.0)])
def test_due_score_examples(elapsed, expected):
    due = compute_due_score(REF - dt.timedelta(days=elapsed), REF, 3)
    assert round(due.due_score, 2) == expected and due.days_since_last_purchase == elapsed


@pytest.mark.parametrize("last,interval", [
    (REF - dt.timedelta(days=2), None), (REF - dt.timedelta(days=2), 0), (REF - dt.timedelta(days=2), -4),
    (REF - dt.timedelta(days=2), float("nan")), (None, 5), (REF + dt.timedelta(days=1), 5),
])
def test_due_score_not_measurable_never_crashes(last, interval):
    due = compute_due_score(last, REF, interval)
    assert due.due_score is None and due.due_ratio is None
    assert classify_replenishment(due, CFG.replenishment_status) is ReplenishmentStatus.INSUFFICIENT_HISTORY


@pytest.mark.parametrize("elapsed,interval,status", [
    (4, 10, "Not Due Yet"), (5, 10, "Due Soon"), (8, 10, "Approaching Due"), (10, 10, "Due"),
    (12, 10, "Due"), (13, 10, "Overdue"), (40, 10, "Overdue"),
])
def test_replenishment_status(elapsed, interval, status):
    due = compute_due_score(REF - dt.timedelta(days=elapsed), REF, interval)
    assert classify_replenishment(due, CFG.replenishment_status).value == status


# ------------------------------------------------------------------ habit
def test_habit_score_uses_category_thresholds_and_default():
    h = CFG.habit
    assert compute_habit_score(5, "daily_perishable", h).habit_score == pytest.approx(5 / 10)
    assert compute_habit_score(2, "monthly_staple", h).habit_score == pytest.approx(2 / 4)
    assert compute_habit_score(40, "monthly_staple", h).habit_score == 1.0
    unknown = compute_habit_score(3, "space_food", h)
    assert unknown.threshold == h.default_threshold and unknown.threshold_source == "default"
    assert compute_habit_score(3, None, h).threshold == h.default_threshold


# ------------------------------------------------------------------ consistency
def test_consistency_needs_two_intervals_and_never_fakes_a_perfect_score():
    for intervals in ([], [7]):
        c = compute_consistency_score(intervals, CFG.consistency)
        assert c.consistency_score is None and c.status == INSUFFICIENT_HISTORY
    perfect = compute_consistency_score([7, 7, 7], CFG.consistency)
    assert perfect.consistency_score == 1.0 and perfect.status == SUFFICIENT_HISTORY


def test_consistency_uses_sample_std():
    c = compute_consistency_score([2, 4], CFG.consistency)  # sample std = sqrt(2), mean 3
    assert c.coefficient_of_variation == pytest.approx(2 ** 0.5 / 3)
    assert c.consistency_score == pytest.approx(1 / (1 + 2 ** 0.5 / 3))
    assert (compute_consistency_score([7, 8, 7, 6], CFG.consistency).consistency_score
            > compute_consistency_score([2, 20, 5, 14], CFG.consistency).consistency_score)


# ------------------------------------------------------------------ final score
def test_final_score_formula_and_fallbacks():
    f = compute_final_score(0.8, 1.0, 0.9, CFG.weights, CFG.fallbacks)
    assert f.final_score == pytest.approx(0.5 * 0.8 + 0.3 * 1.0 + 0.2 * 0.9) and f.fallbacks_applied == ()
    g = compute_final_score(None, 0.25, None, CFG.weights, CFG.fallbacks)
    assert g.fallbacks_applied == ("due_score", "consistency_score")
    assert g.final_score == pytest.approx(0.3 * 0.25 + 0.2 * CFG.fallbacks.consistency_score)
    assert compute_final_score(1, 1, 1, CFG.weights, CFG.fallbacks).final_score == pytest.approx(1.0)


# ------------------------------------------------------------------ quantity
@pytest.mark.parametrize("qtys,expected", [([1, 1, 2, 1, 1], 1), ([5, 5, 10, 5], 5), ([1, 2], 2),
                                           ([3, None, 3], 3), ([None, None], None)])
def test_quantity_estimate(qtys, expected):
    occasions = [occ(REF - dt.timedelta(days=i), q) for i, q in enumerate(qtys)]
    assert estimate_quantity(occasions, CFG.quantity).suggested_quantity == expected


# ------------------------------------------------------------------ config
def _cfg(**changes):
    raw = load_raw_config()
    for path, value in changes.items():
        section, key = path.split("__")
        raw[section][key] = value
    return raw


@pytest.mark.parametrize("changes,message", [
    ({"weights__due": 0.6}, "sum to 1"),
    ({"replenishment_status__due_soon_min": 0.9}, "due_soon_min < approaching_due_min"),
    ({"history__reference_date": "yesterday"}, "reference_date"),
    ({"consistency__min_intervals": 1}, "min_intervals"),
    ({"limits__default_limit": 60}, "default_limit"),
    ({"habit__default_threshold": 0}, "default_threshold"),
    ({"fallbacks__consistency_score": 1.5}, "consistency_score"),
])
def test_invalid_config_is_rejected(changes, message):
    with pytest.raises(ConfigError, match=message):
        parse_config(_cfg(**changes))


def test_bundled_config_values():
    assert (CFG.weights.due, CFG.weights.habit, CFG.weights.consistency) == (0.5, 0.3, 0.2)
    assert (CFG.limits.default_limit, CFG.limits.max_limit) == (10, 50)
    assert CFG.history.order_sources == ("manual",)


# ------------------------------------------------------------------ engine
def _engine(records, products, **kw):
    return RecommendationEngine(repository=repo(records, products, **kw), config=CFG)


def test_ranks_all_products_before_applying_the_limit():
    # 12 products; the most overdue one sorts LAST by id, so "take 10 then rank" would miss it.
    products = [product(f"P{i:02d}") for i in range(12)]
    records = []
    for i in range(11):
        records += purchases("C1", f"P{i:02d}", [30, 25, 20, 15, 10, 5, 0])   # just bought: not due
    records += purchases("C1", "P11", [38, 33, 28, 23, 18, 13, 8])            # overdue, not lapsed
    result = _engine(records, products).generate_recommendations("C1")
    assert result.total_eligible_products == 12 and len(result.recommendations) == 10
    assert result.recommendations[0].product_id == "P11"
    assert [r.rank for r in result.recommendations] == list(range(1, 11))
    scores = [r.final_score for r in result.recommendations]
    assert scores == sorted(scores, reverse=True)


def test_ties_break_by_product_id_and_quantity_never_affects_score():
    records = purchases("C1", "B", [20, 10, 0], qty=9) + purchases("C1", "A", [20, 10, 0], qty=1)
    r = _engine(records, [product("A"), product("B")]).generate_recommendations("C1").recommendations
    assert [x.product_id for x in r] == ["A", "B"] and r[0].final_score == r[1].final_score
    assert (r[0].suggested_quantity, r[1].suggested_quantity) == (1, 9)


def test_eligibility_rules_and_no_filler_products():
    products = [product("BOUGHT"), product("NEVER"), product("DURABLE", replenishable=False), product("SUB")]
    records = (purchases("C1", "BOUGHT", [9, 6, 3]) + purchases("C1", "DURABLE", [9, 3])
               + purchases("C1", "SUB", [9, 6, 3]) + purchases("C1", "GHOST", [4])   # GHOST not in catalogue
               + purchases("C2", "NEVER", [5, 2]))                                  # someone else's purchases
    result = _engine(records, products, customers=("C1", "C2"),
                     subscriptions={"C1": {"SUB"}}).generate_recommendations("C1", limit=10)
    assert [r.product_id for r in result.recommendations] == ["BOUGHT"]
    assert result.excluded_products == {"not_replenishable": 1, "already_subscribed": 1, "not_in_catalogue": 1}


def test_customer_without_history_gets_an_empty_list():
    result = _engine([], [product("A")], customers=("C1",)).generate_recommendations("C1")
    assert result.recommendations == [] and result.total_eligible_products == 0


@pytest.mark.parametrize("bad", [0, 51, -1, True, "5", 2.0])
def test_invalid_limit(bad):
    with pytest.raises(InvalidRequestError):
        _engine([], []).generate_recommendations("C1", limit=bad)


@pytest.mark.parametrize("bad", ["", "   ", None, "x" * 65])
def test_invalid_customer_id(bad):
    with pytest.raises(InvalidRequestError):
        _engine([], []).generate_recommendations(bad)


def test_unknown_customer():
    with pytest.raises(CustomerNotFoundError):
        _engine([], []).generate_recommendations("NOBODY")


def test_reference_date_defaults_to_latest_order_and_as_of_hides_later_purchases():
    records = purchases("C1", "A", [40, 30, 20, 10]) + purchases("C2", "A", [0])
    engine = _engine(records, [product("A")], customers=("C1", "C2"))
    assert engine.generate_recommendations("C1").reference_date == REF
    past = engine.generate_recommendations("C1", as_of=(REF - dt.timedelta(days=25)).isoformat())
    assert past.recommendations[0].purchase_count == 2  # purchases after as_of are ignored
    with pytest.raises(InvalidRequestError):
        engine.generate_recommendations("C1", as_of="15/11/2026")


def test_explanations_reflect_the_numbers():
    products = [product("ONCE"), product("REG")]
    records = purchases("C1", "ONCE", [3]) + purchases("C1", "REG", [72, 62, 52, 42, 32, 22, 12])
    result = _engine(records, products).generate_recommendations("C1", as_of=REF)
    recs = {r.product_id: r for r in result.recommendations}
    once = recs["ONCE"]
    assert once.replenishment_status == "Insufficient History" and once.due_score is None
    assert "not enough purchase history" in once.explanation and "regularly" not in once.explanation
    assert once.fallbacks_applied == ("due_score", "consistency_score")
    reg = recs["REG"]
    assert "about every 10 days" in reg.explanation and "12 days ago" in reg.explanation
    assert reg.replenishment_status == "Due" and "due for a reorder" in reg.explanation


def test_result_serializes_to_json_ready_dict():
    d = result_to_dict(_engine(purchases("C1", "A", [20, 10, 0]), [product("A")]).generate_recommendations("C1"))
    assert d["reference_date"] == REF.isoformat() and d["recommendations"][0]["last_purchase_date"] == REF.isoformat()
    assert d["total_recommendations"] == 1


def test_engine_is_framework_independent():
    code = ("import sys, recommendation_engine, recommendation_engine.engine; "
            "bad = [m for m in ('fastapi', 'starlette', 'flask', 'app') if m in sys.modules]; "
            "assert not bad, bad")
    subprocess.run([sys.executable, "-c", code], cwd=ROOT, check=True)


# ------------------------------------------------------------------ lapse
from recommendation_engine.scoring import compute_lapse  # noqa: E402


@pytest.mark.parametrize("since,interval,lapsed,factor", [
    (2, 2, False, 1.0),      # milk on time
    (9, 2, False, 1.0),      # milk after a week's holiday: min_extra_days keeps it active
    (11, 2, True, 0.5),      # 1 interval past lapse start (9 days) -> half
    (40, 2, True, 0.1),      # long gone -> floor
    (60, 30, False, 1.0),    # rice: lapse starts at 2 x 30 = 60 days
    (90, 30, True, 0.5),
])
def test_lapse_factor(since, interval, lapsed, factor):
    lapse = compute_lapse(compute_due_score(REF - dt.timedelta(days=since), REF, interval), CFG.lapse)
    assert lapse.lapsed is lapsed and lapse.lapse_factor == pytest.approx(factor)


def test_no_lapse_judgement_without_history_or_when_disabled():
    assert compute_lapse(compute_due_score(REF, REF, None), CFG.lapse).lapse_factor == 1.0
    raw = load_raw_config()
    raw["lapse"]["enabled"] = False
    off = parse_config(raw).lapse
    assert compute_lapse(compute_due_score(REF - dt.timedelta(days=99), REF, 2), off).lapse_factor == 1.0


def test_lapsed_habits_rank_below_current_needs():
    """A customer who stopped buying milk 40 days ago (every 2 days before) but whose
    shampoo is due now: shampoo first, milk labelled Lapsed and explained honestly."""
    records = (purchases("C1", "MILK", list(range(100, 38, -2)))         # stopped 40 days ago
               + purchases("C1", "SHAMPOO", [130, 90, 50, 10]))          # every 40 days, due in ~30
    products = [product("MILK"), product("SHAMPOO", pattern="personal_care")]
    recs = _engine(records, products).generate_recommendations("C1", as_of=REF + dt.timedelta(days=30)).recommendations
    assert [r.product_id for r in recs] == ["SHAMPOO", "MILK"]
    milk = recs[1]
    assert milk.replenishment_status == "Lapsed" and milk.due_score == 1.0  # Due Score itself unchanged
    assert milk.lapse_factor == pytest.approx(CFG.lapse.floor)
    assert milk.final_score == pytest.approx(CFG.lapse.floor * (0.5 + 0.3 + 0.2 * milk.consistency_score))
    assert "may no longer be part of your routine" in milk.explanation
    assert recs[0].lapse_factor == 1.0


def test_lapse_config_must_follow_overdue():
    raw = load_raw_config()
    raw["lapse"]["start_ratio"] = 1.1
    with pytest.raises(ConfigError, match="start_ratio"):
        parse_config(raw)
