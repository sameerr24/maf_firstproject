"""Tests for scripts/generate_synthetic_transactions.py.

The generator runs once into a temp directory (session fixture). The committed
files in data/synthetic/ are also checked so stale outputs are caught.
"""
import copy

import pandas as pd
import pytest

from scripts import generate_synthetic_transactions as gen


@pytest.fixture(scope="session")
def syn_run(tmp_path_factory):
    out = tmp_path_factory.mktemp("synthetic")
    cat_path = gen._resolve(gen.load_config()["catalogue_csv"])
    sha_before = gen.file_sha256(cat_path)
    res = gen.run(output_dir=out, verbose=False)
    res["out"], res["cat_path"], res["cat_sha_before"] = out, cat_path, sha_before
    return res


@pytest.fixture(scope="session")
def tables(syn_run):
    return {n: pd.read_csv(syn_run["paths"][n]) for n in ("customers", "orders", "order_items")}


# ------------------------------------------------------------- required checks
def test_output_files_exist(syn_run):
    for p in syn_run["paths"].values():
        assert p.exists() and p.stat().st_size > 0


def test_customer_count_and_unique_ids(tables, syn_run):
    c = tables["customers"]
    assert len(c) == syn_run["config"]["n_customers"] == 100
    assert c["customer_id"].is_unique


def test_orders_reference_valid_customers(tables):
    assert set(tables["orders"]["customer_id"]) <= set(tables["customers"]["customer_id"])


def test_order_items_reference_valid_orders(tables):
    items, orders = tables["order_items"], tables["orders"]
    assert orders["order_id"].is_unique
    assert set(items["order_id"]) <= set(orders["order_id"])
    assert set(orders["order_id"]) <= set(items["order_id"]), "every order needs at least one item"


def test_product_ids_come_from_processed_catalogue(tables, syn_run):
    assert set(tables["order_items"]["product_id"]) <= set(syn_run["catalogue"]["product_id"])


def test_quantities_are_positive_integers(tables):
    q = tables["order_items"]["quantity"]
    assert pd.api.types.is_integer_dtype(q)
    assert (q > 0).all()


def test_dates_are_valid_and_inside_window(tables, syn_run):
    w = syn_run["data"]["window"]
    od = pd.to_datetime(tables["orders"]["order_date"], format="%Y-%m-%d")
    assert od.min() >= pd.Timestamp(w.start) and od.max() <= pd.Timestamp(w.end)
    assert (w.end - w.start).days + 1 == 180
    pd.to_datetime(tables["orders"]["order_datetime"], format="ISO8601")
    signup = tables["orders"]["customer_id"].map(
        tables["customers"].set_index("customer_id")["signup_date"].pipe(pd.to_datetime))
    assert (od >= signup).all()


def test_no_duplicate_product_in_same_order(tables):
    assert not tables["order_items"].duplicated(["order_id", "product_id"]).any()


def test_one_order_per_customer_per_day(tables):
    assert not tables["orders"].duplicated(["customer_id", "order_date"]).any()


def test_catalogue_not_modified(syn_run):
    assert gen.file_sha256(syn_run["cat_path"]) == syn_run["cat_sha_before"]


def test_generation_is_reproducible(syn_run, tmp_path):
    again = gen.run(output_dir=tmp_path, verbose=False)
    for name, path in syn_run["paths"].items():
        assert path.read_bytes() == again["paths"][name].read_bytes(), f"{name} differs between runs"


def test_different_seed_changes_data(syn_run, tmp_path):
    other = gen.run(output_dir=tmp_path, seed=syn_run["config"]["seed"] + 1, verbose=False)
    assert other["paths"]["orders"].read_bytes() != syn_run["paths"]["orders"].read_bytes()


def test_committed_outputs_match_generator(syn_run):
    """data/synthetic/ must be what the current config + seed produce."""
    committed = gen._resolve(syn_run["config"]["output_dir"])
    for name, path in syn_run["paths"].items():
        assert (committed / path.name).read_bytes() == path.read_bytes(), \
            f"data/synthetic/{path.name} is stale; re-run scripts/generate_synthetic_transactions.py"


def test_profile_purchase_occasions_match_order_items(syn_run, tables):
    prof = pd.read_csv(syn_run["paths"]["customer_product_profiles"])
    assert not prof.duplicated(["customer_id", "product_id"]).any()
    items = tables["order_items"].merge(tables["orders"][["order_id", "customer_id"]], on="order_id")
    actual = items.groupby(["customer_id", "product_id"]).size()
    expected = prof.set_index(["customer_id", "product_id"])["purchase_occasions"]
    assert set(actual.index) <= set(expected.index), "purchased pairs without a profile"
    assert (expected.reindex(actual.index) == actual).all()
    assert (expected.drop(actual.index) == 0).all()


# ------------------------------------------------------- behavioural realism
def test_all_segments_present(tables, syn_run):
    assert set(tables["customers"]["behaviour_segment"]) == set(syn_run["config"]["segments"])


def test_customers_buy_different_subsets(tables):
    items = tables["order_items"].merge(tables["orders"][["order_id", "customer_id"]], on="order_id")
    per_cust = items.groupby("customer_id")["product_id"].nunique()
    n_products = items["product_id"].nunique()
    assert (per_cust < n_products).mean() > 0.9, "most customers should not buy every product"
    assert per_cust.nunique() > 5
    assert len(items.groupby("customer_id")["product_id"].apply(frozenset).unique()) > 90


def test_multi_product_orders_and_multi_unit_lines_exist(tables):
    assert (tables["orders"]["item_count"] >= 2).mean() > 0.5
    assert (tables["order_items"]["quantity"] > 1).any()


def test_frequent_products_bought_more_often_than_slow_ones(tables, syn_run):
    pt = dict(zip(syn_run["catalogue"]["product_id"], syn_run["catalogue"]["product_type"]))
    items = tables["order_items"].merge(tables["orders"][["order_id", "customer_id"]], on="order_id")
    items["ptype"] = items["product_id"].map(pt)
    occ = items.groupby(["customer_id", "ptype"]).size().groupby("ptype").median()
    for fast in ("milk", "tomato", "onion"):
        for slow in ("rice", "shampoo", "detergent"):
            assert occ[fast] > occ[slow], f"{fast} should be bought more often than {slow}"


def test_histories_are_not_perfectly_periodic(tables):
    items = tables["order_items"].merge(tables["orders"][["order_id", "customer_id", "order_date"]], on="order_id")
    items["order_date"] = pd.to_datetime(items["order_date"])
    items = items.sort_values(["customer_id", "product_id", "order_date"])
    gaps = items.groupby(["customer_id", "product_id"])["order_date"].diff().dt.days.dropna()
    per_pair_unique = items.assign(g=items.groupby(["customer_id", "product_id"])["order_date"].diff().dt.days) \
        .dropna().groupby(["customer_id", "product_id"])["g"].nunique()
    assert gaps.nunique() > 10
    assert (per_pair_unique > 1).mean() > 0.8, "most histories should have varying gaps"


def test_edge_cases_are_present(tables, syn_run):
    w = syn_run["data"]["window"]
    orders = tables["orders"]
    last = pd.to_datetime(orders.groupby("customer_id")["order_date"].max())
    days_since = (pd.Timestamp(w.end) - last).dt.days
    assert (days_since > 30).sum() >= 5, "need customers with no recent purchases"
    assert (days_since <= 2).sum() >= 20, "need recently active customers"

    hist = gen.purchase_history_stats(orders, tables["order_items"], w.end)
    hist["scenario"] = hist.apply(gen.scenario_label, axis=1)
    counts = hist["scenario"].value_counts()
    for s in ("recently purchased", "not yet due", "overdue (>2x usual gap)", "insufficient history (<3 purchases)"):
        assert counts.get(s, 0) >= 20, f"too few '{s}' pairs"
    assert ((hist["occasions"] >= 4) & (hist["gap_cv"] > 0.6)).sum() >= 20, "need inconsistent histories"


def test_lapsed_customers_stop_buying(tables):
    lapsed = tables["customers"].query("behaviour_segment == 'lapsed'")["customer_id"]
    o = tables["orders"][tables["orders"]["customer_id"].isin(lapsed)]
    assert o.groupby("customer_id").size().min() >= 5, "lapsed customers should have prior history"
    last = pd.to_datetime(o.groupby("customer_id")["order_date"].max())
    end = pd.to_datetime(tables["orders"]["order_date"]).max()
    assert ((end - last).dt.days >= 29).all()


def test_weekly_buyers_shop_on_one_weekday(tables):
    c = tables["customers"].query("behaviour_segment == 'weekly'").set_index("customer_id")
    o = tables["orders"][tables["orders"]["customer_id"].isin(c.index)].copy()
    o["wd"] = pd.to_datetime(o["order_date"]).dt.strftime("%a")
    share = o.groupby("customer_id").apply(lambda g: (g["wd"] == c.loc[g.name, "preferred_weekday"]).mean(),
                                            include_groups=False)
    assert (share > 0.9).all()


# ------------------------------------------------------------- validator tests
def _break(tables, fn):
    t = {k: v.copy() for k, v in tables.items()}
    fn(t)
    return t


@pytest.mark.parametrize("mutate,msg", [
    (lambda t: t["orders"].loc.__setitem__((0, "customer_id"), "CUST-9999"), "unknown customer_id"),
    (lambda t: t["order_items"].loc.__setitem__((0, "order_id"), "ORD-999999"), "unknown order_id"),
    (lambda t: t["order_items"].loc.__setitem__((0, "product_id"), "bb-0"), "not in the processed catalogue"),
    (lambda t: t["order_items"].loc.__setitem__((0, "quantity"), 0), "non-positive quantity"),
    (lambda t: t["orders"].loc.__setitem__((0, "order_date"), "2026-13-45"), "invalid dates"),
])
def test_validator_catches_bad_data(tables, syn_run, mutate, msg):
    t = _break(tables, mutate)
    with pytest.raises(gen.SyntheticDataError, match=msg):
        gen.validate_dataset(t["customers"], t["orders"], t["order_items"], syn_run["catalogue"], syn_run["config"])


def test_validator_catches_duplicate_order_item(tables, syn_run):
    t = copy.deepcopy(tables)
    dup = t["order_items"].iloc[[0]].assign(order_item_id="OI-X")
    t["order_items"] = pd.concat([t["order_items"], dup], ignore_index=True)
    with pytest.raises(gen.SyntheticDataError, match="duplicate \\(order_id, product_id\\)"):
        gen.validate_dataset(t["customers"], t["orders"], t["order_items"], syn_run["catalogue"], syn_run["config"])


def test_config_rejects_unknown_product_type(syn_run):
    cfg = copy.deepcopy(syn_run["config"])
    cfg["product_profiles"]["caviar"] = {"base_interval_days": 5, "base_quantity": 1, "interval_cv": 0.2, "adoption": 0.1}
    with pytest.raises(gen.SyntheticDataError, match="not in the catalogue"):
        gen.validate_config(cfg, syn_run["catalogue"])
