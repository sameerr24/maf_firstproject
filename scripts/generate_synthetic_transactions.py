"""Generate a reproducible synthetic purchase history for the curated catalogue.

Reads the Phase 1 catalogue (data/processed/selected_products.csv, read-only) and
config/synthetic_config.json, then writes to data/synthetic/:

    customers.csv                  one row per synthetic customer
    orders.csv                     one row per purchase occasion (order)
    order_items.csv                one row per (order, product); quantity = units of that pack
    customer_product_profiles.csv  generator ground truth (intended cycle per customer x product)
    synthetic_data_report.md       assumptions + dataset statistics

Run from the project root:
    python scripts/generate_synthetic_transactions.py [--seed N] [--customers N]

Key modelling rule: quantity is independent of purchase frequency. Buying 5 units
in one order is ONE purchase occasion (one order_items row with quantity=5).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "synthetic_config.json"

CUSTOMER_COLUMNS = ["customer_id", "signup_date", "behaviour_segment", "household_size", "preferred_weekday"]
ORDER_COLUMNS = ["order_id", "customer_id", "order_date", "order_datetime", "item_count", "unit_count", "order_total"]
ITEM_COLUMNS = ["order_item_id", "order_id", "product_id", "product_type", "quantity", "unit_price", "line_total"]
PROFILE_COLUMNS = [
    "customer_id", "product_id", "product_type", "intended_interval_days", "intended_mean_quantity",
    "active_from", "active_until", "late_adopter", "dropped_out", "purchase_occasions",
]
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


class SyntheticDataError(Exception):
    """Raised when configuration or generated data is invalid."""


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #
def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _strip_comments(obj):
    if isinstance(obj, dict):
        return {k: _strip_comments(v) for k, v in obj.items() if not k.startswith("_")}
    return obj


def load_config(path: Path = DEFAULT_CONFIG, overrides: dict | None = None) -> dict:
    try:
        cfg = _strip_comments(json.loads(Path(path).read_text(encoding="utf-8")))
    except FileNotFoundError:
        raise SyntheticDataError(f"Config not found: {path}")
    except json.JSONDecodeError as e:
        raise SyntheticDataError(f"Config {path} is not valid JSON: {e}")
    cfg.update({k: v for k, v in (overrides or {}).items() if v is not None})
    return cfg


def _resolve(p: str) -> Path:
    path = Path(p)
    return path if path.is_absolute() else PROJECT_ROOT / path


def load_catalogue(cfg: dict) -> pd.DataFrame:
    path = _resolve(cfg["catalogue_csv"])
    if not path.exists():
        raise SyntheticDataError(f"Processed catalogue not found at {path}. Run scripts/preprocess_catalogue.py first.")
    cat = pd.read_csv(path)
    for col in ("product_id", "product_type", "discount_price"):
        if col not in cat.columns:
            raise SyntheticDataError(f"Catalogue {path} is missing column '{col}'.")
    if cat["product_type"].duplicated().any():
        raise SyntheticDataError("Catalogue has duplicate product_type values; expected one SKU per type.")
    return cat


def validate_config(cfg: dict, catalogue: pd.DataFrame) -> None:
    errors = []
    profiles, segments = cfg["product_profiles"], cfg["segments"]
    cat_types = set(catalogue["product_type"])
    if set(profiles) - cat_types:
        errors.append(f"product_profiles has types not in the catalogue: {sorted(set(profiles) - cat_types)}")
    if cat_types - set(profiles):
        errors.append(f"Catalogue types without a product profile: {sorted(cat_types - set(profiles))}")
    total = sum(s["count"] for s in segments.values())
    if total != cfg["n_customers"]:
        errors.append(f"Segment counts sum to {total}, but n_customers is {cfg['n_customers']}.")
    for name, s in segments.items():
        lo, hi = s["products"]
        if not (1 <= lo <= hi):
            errors.append(f"Segment '{name}': invalid products range {s['products']}.")
    for t, p in profiles.items():
        if p["base_interval_days"] <= 0 or p["base_quantity"] <= 0:
            errors.append(f"Profile '{t}': base_interval_days and base_quantity must be positive.")
    if cfg["history_days"] < 30:
        errors.append("history_days should be at least 30.")
    if errors:
        raise SyntheticDataError("Invalid synthetic config:\n  - " + "\n  - ".join(errors))


def scale_segment_counts(segments: dict, n: int) -> dict:
    """Rescale segment counts to a different customer total (used by --customers)."""
    total = sum(s["count"] for s in segments.values())
    raw = {k: s["count"] * n / total for k, s in segments.items()}
    counts = {k: int(v) for k, v in raw.items()}
    for k in sorted(raw, key=lambda k: raw[k] - counts[k], reverse=True)[: n - sum(counts.values())]:
        counts[k] += 1
    return {k: {**s, "count": counts[k]} for k, s in segments.items()}


# --------------------------------------------------------------------------- #
# Simulation
# --------------------------------------------------------------------------- #
@dataclass
class Window:
    start: date
    end: date

    @property
    def days(self) -> int:
        return (self.end - self.start).days


def _lognormal(rng, sigma: float) -> float:
    """Mean-one lognormal noise."""
    return float(rng.lognormal(-sigma ** 2 / 2, sigma)) if sigma > 0 else 1.0


def simulate_customer(idx: int, segment_name: str, cfg: dict, catalogue: pd.DataFrame, window: Window):
    seg = cfg["segments"][segment_name]
    irr = cfg["irregularities"]
    rng = np.random.default_rng([cfg["seed"], idx])  # per-customer stream: stable if others change
    cid = f"CUST-{idx + 1:04d}"

    hh_sizes = list(irr["household_size_weights"])
    hh_w = np.array([irr["household_size_weights"][k] for k in hh_sizes], dtype=float)
    household = int(rng.choice(hh_sizes, p=hh_w / hh_w.sum()))
    hh_factor = (household / 3) ** 0.7

    # Signup / active period
    if "signup_days_before_end" in seg:
        lo, hi = seg["signup_days_before_end"]
        signup = window.end - timedelta(days=int(rng.integers(lo, hi + 1)))
    elif rng.random() < 0.8:
        signup = window.start - timedelta(days=int(rng.integers(1, 240)))
    else:
        signup = window.start + timedelta(days=int(rng.integers(0, 45)))
    active_start = max(signup, window.start)
    active_end = window.end
    if "churn_days_before_end" in seg:
        lo, hi = seg["churn_days_before_end"]
        active_end = window.end - timedelta(days=int(rng.integers(lo, hi + 1)))
    span = (active_end - active_start).days

    vacation = None
    if span > 40 and rng.random() < irr["vacation_probability"]:
        vlen = int(rng.integers(irr["vacation_length_days"][0], irr["vacation_length_days"][1] + 1))
        vstart = active_start + timedelta(days=int(rng.integers(5, max(6, span - vlen))))
        vacation = (vstart, vstart + timedelta(days=vlen))

    weekday = int(rng.integers(0, 7))
    pref_hour = float(rng.uniform(8, 21))

    # Which products this customer buys
    types = list(catalogue["product_type"])
    adoption = np.array([cfg["product_profiles"][t]["adoption"] for t in types], dtype=float)
    k = min(int(rng.integers(seg["products"][0], seg["products"][1] + 1)), len(types))
    chosen = sorted(rng.choice(types, size=k, replace=False, p=adoption / adoption.sum()),
                    key=types.index)

    events, profiles = [], []
    is_new = "signup_days_before_end" in seg
    for ptype in chosen:
        prof = cfg["product_profiles"][ptype]
        base = prof["base_interval_days"]
        interval = base * seg["interval_multiplier"] * _lognormal(rng, irr["personal_interval_sigma"])
        interval = max(interval, seg["min_interval_days"])
        if seg.get("weekly_snap"):
            interval = max(7, round(interval / 7) * 7)
        cv = prof["interval_cv"] + seg["extra_interval_cv"]
        # Consumption-rate preserving quantity: longer gaps -> more units per occasion.
        mean_qty = prof["base_quantity"] * hh_factor * max(1.0, interval / base)

        p_start, p_end = active_start, active_end
        late = (not is_new) and span > 60 and rng.random() < irr["late_adoption_probability"]
        if late:
            p_start = active_start + timedelta(days=int(span * rng.uniform(0.2, 0.6)))
        dropped = (not is_new) and span > 60 and rng.random() < irr["product_dropout_probability"]
        if dropped:
            p_end = p_start + timedelta(days=int((p_end - p_start).days * rng.uniform(0.3, 0.7)))

        first_window = min(interval, max(1.0, (p_end - p_start).days)) if is_new else interval
        t = float(rng.uniform(0, first_window))
        while True:
            day = p_start + timedelta(days=int(t))
            if vacation and vacation[0] <= day < vacation[1]:
                day = vacation[1] + timedelta(days=int(rng.integers(0, 3)))
                t = float((day - p_start).days)
            if seg.get("weekly_snap"):
                day = day + timedelta(days=(weekday - day.weekday()) % 7)
            if day > p_end:
                break
            if rng.random() >= seg["skip_probability"]:
                qty = max(1, round(mean_qty * _lognormal(rng, irr["quantity_sigma"])))
                events.append((day, ptype, min(qty, cfg["max_quantity_per_line"])))
            t += max(1.0, interval * _lognormal(rng, cv))

        profiles.append({
            "customer_id": cid, "product_type": ptype,
            "intended_interval_days": round(interval, 1), "intended_mean_quantity": round(mean_qty, 2),
            "active_from": p_start.isoformat(), "active_until": p_end.isoformat(),
            "late_adopter": late, "dropped_out": dropped or "churn_days_before_end" in seg,
            "purchase_occasions": 0,  # filled in after orders are grouped
        })

    # Group product purchases into orders (shopping trips).
    events.sort(key=lambda e: (e[0], types.index(e[1])))
    orders, current = [], None
    for day, ptype, qty in events:
        if current is None or (day - current["date"]).days > seg["order_window_days"]:
            current = {"date": day, "items": {}}
            orders.append(current)
        # Same product twice inside one trip -> one line with combined quantity.
        current["items"][ptype] = min(current["items"].get(ptype, 0) + qty, cfg["max_quantity_per_line"])

    # Count occasions AFTER grouping: merged same-trip purchases are one occasion,
    # so this equals the number of order_items rows for the pair.
    for prof in profiles:
        prof["purchase_occasions"] = sum(prof["product_type"] in o["items"] for o in orders)

    for o in orders:
        hour = float(np.clip(rng.normal(pref_hour, 1.5), 6, 23.5))
        o["datetime"] = datetime.combine(o["date"], datetime.min.time()) + timedelta(
            hours=int(hour), minutes=int((hour % 1) * 60), seconds=int(rng.integers(0, 60)))

    customer = {
        "customer_id": cid, "signup_date": signup.isoformat(), "behaviour_segment": segment_name,
        "household_size": household, "preferred_weekday": WEEKDAYS[weekday],
    }
    return customer, orders, profiles


def generate(cfg: dict, catalogue: pd.DataFrame) -> dict[str, pd.DataFrame]:
    end = date.fromisoformat(cfg["history_end_date"])
    window = Window(end - timedelta(days=cfg["history_days"] - 1), end)

    # Assign segments in a seeded shuffled order so IDs don't cluster by behaviour.
    labels = [name for name, s in cfg["segments"].items() for _ in range(s["count"])]
    labels = list(np.random.default_rng(cfg["seed"]).permutation(labels))

    pid = dict(zip(catalogue["product_type"], catalogue["product_id"]))
    price = dict(zip(catalogue["product_type"], catalogue["discount_price"].astype(float)))

    customers, raw_orders, profiles = [], [], []
    for i, seg in enumerate(labels):
        c, orders, prof = simulate_customer(i, seg, cfg, catalogue, window)
        customers.append(c)
        profiles.extend(prof)
        raw_orders.extend((c["customer_id"], o) for o in orders)

    raw_orders.sort(key=lambda x: (x[1]["datetime"], x[0]))
    order_rows, item_rows = [], []
    for n, (cid, o) in enumerate(raw_orders, 1):
        oid = f"ORD-{n:06d}"
        total = units = 0
        for ptype, qty in sorted(o["items"].items(), key=lambda kv: pid[kv[0]]):
            line = round(qty * price[ptype], 2)
            item_rows.append({"order_id": oid, "product_id": pid[ptype], "product_type": ptype,
                              "quantity": int(qty), "unit_price": price[ptype], "line_total": line})
            total += line
            units += qty
        order_rows.append({"order_id": oid, "customer_id": cid, "order_date": o["date"].isoformat(),
                           "order_datetime": o["datetime"].isoformat(), "item_count": len(o["items"]),
                           "unit_count": int(units), "order_total": round(total, 2)})

    items = pd.DataFrame(item_rows)
    items.insert(0, "order_item_id", [f"OI-{i:07d}" for i in range(1, len(items) + 1)])
    prof_df = pd.DataFrame(profiles)
    prof_df.insert(1, "product_id", prof_df["product_type"].map(pid))
    return {
        "customers": pd.DataFrame(customers, columns=CUSTOMER_COLUMNS),
        "orders": pd.DataFrame(order_rows, columns=ORDER_COLUMNS),
        "order_items": items[ITEM_COLUMNS],
        "customer_product_profiles": prof_df[PROFILE_COLUMNS],
        "window": window,
    }


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #
def validate_dataset(customers: pd.DataFrame, orders: pd.DataFrame, items: pd.DataFrame,
                     catalogue: pd.DataFrame, cfg: dict) -> None:
    errors = []

    def check(cond, msg):
        if not cond:
            errors.append(msg)

    for df, name, cols in ((customers, "customers", CUSTOMER_COLUMNS), (orders, "orders", ORDER_COLUMNS),
                           (items, "order_items", ITEM_COLUMNS)):
        missing = [c for c in cols if c not in df.columns]
        check(not missing, f"{name} is missing columns {missing}")
    if errors:
        raise SyntheticDataError("Validation failed:\n  - " + "\n  - ".join(errors))

    check(customers["customer_id"].is_unique, "customer_id values are not unique")
    check(len(customers) == cfg["n_customers"], f"Expected {cfg['n_customers']} customers, found {len(customers)}")
    check(orders["order_id"].is_unique, "order_id values are not unique")
    check(items["order_item_id"].is_unique, "order_item_id values are not unique")

    bad_cust = set(orders["customer_id"]) - set(customers["customer_id"])
    check(not bad_cust, f"orders reference unknown customer_id(s): {sorted(bad_cust)[:5]}")
    bad_orders = set(items["order_id"]) - set(orders["order_id"])
    check(not bad_orders, f"order_items reference unknown order_id(s): {sorted(bad_orders)[:5]}")
    empty = set(orders["order_id"]) - set(items["order_id"])
    check(not empty, f"{len(empty)} order(s) have no items, e.g. {sorted(empty)[:3]}")
    bad_pid = set(items["product_id"]) - set(catalogue["product_id"])
    check(not bad_pid, f"order_items reference product_id(s) not in the processed catalogue: {sorted(bad_pid)}")
    type_map = dict(zip(catalogue["product_id"], catalogue["product_type"]))
    check((items["product_id"].map(type_map) == items["product_type"]).all(), "product_type does not match product_id")

    q = pd.to_numeric(items["quantity"], errors="coerce")
    check(q.notna().all() and (q == q.round()).all(), "quantity must be an integer")
    check((q > 0).all(), f"{int((q <= 0).sum())} order_items have non-positive quantity")
    check((q <= cfg["max_quantity_per_line"]).all(), "quantity exceeds max_quantity_per_line")
    dup = items.duplicated(["order_id", "product_id"])
    check(not dup.any(), f"{int(dup.sum())} duplicate (order_id, product_id) rows")

    end = date.fromisoformat(cfg["history_end_date"])
    start = end - timedelta(days=cfg["history_days"] - 1)
    od = pd.to_datetime(orders["order_date"], format="%Y-%m-%d", errors="coerce")
    odt = pd.to_datetime(orders["order_datetime"], format="ISO8601", errors="coerce")
    sd = pd.to_datetime(customers["signup_date"], format="%Y-%m-%d", errors="coerce")
    check(od.notna().all() and odt.notna().all(), "orders contain invalid dates")
    check(sd.notna().all(), "customers contain invalid signup_date values")
    check(((od >= pd.Timestamp(start)) & (od <= pd.Timestamp(end))).all(),
          f"order dates fall outside the history window {start}..{end}")
    check((odt.dt.normalize() == od).all(), "order_datetime does not match order_date")
    signup = orders["customer_id"].map(dict(zip(customers["customer_id"], sd)))
    check((od >= signup).all(), "some orders are dated before the customer's signup_date")

    totals = items.groupby("order_id")["line_total"].sum().round(2)
    check(np.allclose(orders.set_index("order_id")["order_total"].reindex(totals.index), totals),
          "order_total does not equal the sum of line_total")
    counts = items.groupby("order_id").size()
    check((orders.set_index("order_id")["item_count"].reindex(counts.index) == counts).all(),
          "item_count does not match the number of order_items")

    if errors:
        raise SyntheticDataError("Validation failed:\n  - " + "\n  - ".join(errors))


# --------------------------------------------------------------------------- #
# Statistics & report
# --------------------------------------------------------------------------- #
def purchase_history_stats(orders: pd.DataFrame, items: pd.DataFrame, end: date) -> pd.DataFrame:
    """Per (customer, product): occasions, observed gaps, and days since last purchase."""
    df = items.merge(orders[["order_id", "customer_id", "order_date"]], on="order_id")
    df["order_date"] = pd.to_datetime(df["order_date"])
    df = df.sort_values(["customer_id", "product_id", "order_date"])
    df["gap"] = df.groupby(["customer_id", "product_id"])["order_date"].diff().dt.days
    g = df.groupby(["customer_id", "product_id", "product_type"])
    out = g.agg(occasions=("order_id", "size"), units=("quantity", "sum"),
                last=("order_date", "max"), median_gap=("gap", "median"),
                mean_gap=("gap", "mean"), std_gap=("gap", "std")).reset_index()
    out["days_since_last"] = (pd.Timestamp(end) - out["last"]).dt.days
    out["gap_cv"] = out["std_gap"] / out["mean_gap"]
    return out


def scenario_label(r) -> str:
    """Descriptive label used ONLY to show the dataset covers the required test cases.
    This is not the recommendation logic."""
    if r.occasions < 3:
        return "insufficient history (<3 purchases)"
    ratio = r.days_since_last / r.median_gap if r.median_gap else np.inf
    if ratio <= 0.5:
        return "recently purchased"
    if ratio <= 1.0:
        return "not yet due"
    if ratio <= 2.0:
        return "due / slightly overdue"
    return "overdue (>2x usual gap)"


def render_report(cfg, data, catalogue, catalogue_sha) -> str:
    customers, orders, items, profiles, window = (data[k] for k in
        ("customers", "orders", "order_items", "customer_product_profiles", "window"))
    hist = purchase_history_stats(orders, items, window.end)
    hist["scenario"] = hist.apply(scenario_label, axis=1)
    inconsistent = hist[(hist["occasions"] >= 4) & (hist["gap_cv"] > 0.6)]
    last_order = pd.to_datetime(orders.groupby("customer_id")["order_date"].max())
    days_since = (pd.Timestamp(window.end) - last_order).dt.days.reindex(customers["customer_id"])
    orders_per = orders.groupby("customer_id").size().reindex(customers["customer_id"]).fillna(0)

    L = [
        "# Synthetic Transaction Data Report", "",
        "_Generated by `scripts/generate_synthetic_transactions.py` from `config/synthetic_config.json`. "
        "**All customers, orders and purchases are synthetic.** Do not edit by hand; change the config and re-run._", "",
        "## Generation settings", "",
        f"- Random seed: `{cfg['seed']}` (same seed + config + catalogue → byte-identical files)",
        f"- History window: **{window.start} → {window.end}** ({cfg['history_days']} days)",
        f"- Catalogue: `{cfg['catalogue_csv']}` ({len(catalogue)} products, sha256 `{catalogue_sha[:16]}…`, read-only)",
        "",
        "## Dataset size", "",
        "| Metric | Value |", "|---|---:|",
        f"| Customers | {len(customers)} |",
        f"| Customers with ≥1 order | {(orders_per > 0).sum()} |",
        f"| Orders (purchase occasions / trips) | {len(orders):,} |",
        f"| Order items (customer × product × order) | {len(items):,} |",
        f"| Units purchased | {int(items['quantity'].sum()):,} |",
        f"| Customer–product pairs with purchases | {len(hist):,} |",
        f"| Mean products per customer | {hist.groupby('customer_id').size().mean():.1f} |",
        f"| Mean items per order | {orders['item_count'].mean():.2f} |",
        f"| Orders with ≥2 products | {(orders['item_count'] >= 2).mean():.0%} |",
        f"| Order-item lines with quantity > 1 | {(items['quantity'] > 1).mean():.0%} |",
        f"| Total order value (₹) | {orders['order_total'].sum():,.0f} |",
        "",
        "## By behaviour segment", "",
        "| Segment | Customers | Orders / customer | Median days between orders | Items / order | Units / line | Days since last order (median) |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    o = orders.merge(customers[["customer_id", "behaviour_segment"]], on="customer_id")
    o["order_date"] = pd.to_datetime(o["order_date"])
    o = o.sort_values(["customer_id", "order_date"])
    o["gap"] = o.groupby("customer_id")["order_date"].diff().dt.days
    it = items.merge(o[["order_id", "behaviour_segment"]], on="order_id")
    for seg in cfg["segments"]:
        cs = customers[customers["behaviour_segment"] == seg]["customer_id"]
        so = o[o["behaviour_segment"] == seg]
        L.append(f"| {seg} | {len(cs)} | {orders_per[cs].mean():.1f} | {so['gap'].median():.0f} | "
                 f"{so['item_count'].mean():.2f} | {it[it['behaviour_segment'] == seg]['quantity'].mean():.2f} | "
                 f"{days_since[cs].median():.0f} |")

    L += ["", "## By product", "",
          "| product_type | product_id | Buyers | Purchase occasions | Units | Mean qty / occasion | Configured base interval (days) | Observed median gap (days) |",
          "|---|---|---:|---:|---:|---:|---:|---:|"]
    gaps = hist.groupby("product_type")["median_gap"].median()
    for _, p in catalogue.iterrows():
        h = hist[hist["product_type"] == p["product_type"]]
        L.append(f"| {p['product_type']} | `{p['product_id']}` | {len(h)} | {int(h['occasions'].sum())} | "
                 f"{int(h['units'].sum())} | {h['units'].sum() / max(1, h['occasions'].sum()):.2f} | "
                 f"{cfg['product_profiles'][p['product_type']]['base_interval_days']} | {gaps.get(p['product_type'], float('nan')):.0f} |")

    bins = pd.cut(orders["item_count"], [0, 1, 3, 6, 100], labels=["1", "2–3", "4–6", "7+"])
    L += ["", "## Basket size (products per order)", "", "| Products in order | Orders |", "|---|---:|"]
    L += [f"| {k} | {v:,} |" for k, v in bins.value_counts().sort_index().items()]

    depth = pd.cut(hist["occasions"], [0, 1, 2, 5, 10, 10_000], labels=["1", "2", "3–5", "6–10", "11+"])
    L += ["", "## History depth per customer–product pair", "", "| Purchase occasions | Pairs |", "|---|---:|"]
    L += [f"| {k} | {v:,} |" for k, v in depth.value_counts().sort_index().items()]

    L += ["", f"## Test-scenario coverage at {window.end}", "",
          "_Descriptive check only: it confirms the edge cases exist. It is **not** the recommendation logic. "
          "Ratio = days since last purchase ÷ that pair's median observed gap._", "",
          "| Scenario (customer–product pairs) | Pairs |", "|---|---:|"]
    order_s = ["recently purchased", "not yet due", "due / slightly overdue", "overdue (>2x usual gap)",
               "insufficient history (<3 purchases)"]
    vc = hist["scenario"].value_counts()
    L += [f"| {s} | {int(vc.get(s, 0)):,} |" for s in order_s]
    L += [f"| inconsistent history (≥4 purchases, gap CV > 0.6) | {len(inconsistent):,} |",
          f"| product dropped out mid-history (ground truth) | {int((profiles['dropped_out'] & ~profiles['customer_id'].isin(customers.loc[customers['behaviour_segment']=='lapsed','customer_id'])).sum())} |",
          f"| product adopted late (ground truth) | {int(profiles['late_adopter'].sum())} |",
          "", "| Scenario (customers) | Customers |", "|---|---:|",
          f"| No order in the last 30 days | {int((days_since > 30).sum())} |",
          f"| No orders at all | {int((orders_per == 0).sum())} |",
          f"| Fewer than 5 orders | {int((orders_per < 5).sum())} |",
          f"| New (signed up in last 45 days) | {int(((pd.Timestamp(window.end) - pd.to_datetime(customers['signup_date'])).dt.days < 45).sum())} |",
          ]

    L += ["", "## Assumptions", "",
          "- **Catalogue only.** Products, prices and product IDs come from `data/processed/selected_products.csv`; "
          "`unit_price` is the catalogue `discount_price` (no price changes over time).",
          "- **Quantity ≠ frequency.** Each `order_items` row is one purchase occasion of one product. `quantity` is "
          "the number of catalogue packs bought (e.g. 3 × 500 ml milk) and never creates extra occasions.",
          "- **Consumption-rate model.** Each product type has a base interval and base quantity for a 3-person "
          "household. A customer's personal interval = base × segment multiplier × personal noise (lognormal). "
          "Quantity scales with household size and with interval/base, so bulk and weekly buyers buy more per trip.",
          "- **Imperfect periodicity.** Every gap is multiplied by lognormal noise (σ = product CV + segment CV); "
          "occasions can be skipped; ~25% of customers take a 6–16 day break (purchases resume afterwards).",
          "- **Orders = shopping trips.** A customer's product purchases within `order_window_days` are merged into one "
          "order dated on the first day; weekly buyers' trips snap to their preferred weekday. At most one order per "
          "customer per day; a product appears at most once per order (quantities combined).",
          "- **Subsets.** Each customer buys a random subset of products (size by segment, weighted by product `adoption`).",
          "- **Bread** is the catalogue's rusk substitute (see Phase 1) but is simulated with a bread-like frequent cycle, "
          "as specified for this phase.",
          "- **Segments** (`behaviour_segment` in customers.csv) and `customer_product_profiles.csv` are generator ground "
          "truth for evaluating a future algorithm. They should not be used as model inputs.",
          "- **Not modelled:** seasonality, promotions, price changes, stock-outs, cancellations/returns, substitution between brands.",
          ""]

    L += ["## Segment definitions", "", "| Segment | Behaviour |", "|---|---|"]
    desc = {
        "regular": "Frequent, fairly regular buyers; many products; near-natural intervals.",
        "weekly": "One shopping trip per week on a preferred weekday; frequent items bought in weekly quantities.",
        "bulk": "Intervals ×2.5 with proportionally larger quantities per occasion; purchases within 4 days merged into one trip.",
        "irregular": "Longer, highly variable gaps (interval ×1.6, high noise) and 30% skipped occasions.",
        "new": "Signed up 7–45 days before the end date; 1–few purchases per product.",
        "lapsed": "Regular history that stops entirely 30–80 days before the end date.",
        "basket_consolidator": "Merges purchases within 5 days into one large multi-product order.",
    }
    L += [f"| {k} | {desc.get(k, '')} |" for k in cfg["segments"]]
    L.append("")
    return "\n".join(L)


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def run(config_path: Path = DEFAULT_CONFIG, output_dir: Path | None = None, seed: int | None = None,
        n_customers: int | None = None, verbose: bool = True) -> dict:
    cfg = load_config(config_path, {"seed": seed})
    if n_customers is not None:
        cfg["segments"] = scale_segment_counts(cfg["segments"], n_customers)
        cfg["n_customers"] = n_customers
    catalogue = load_catalogue(cfg)
    validate_config(cfg, catalogue)
    cat_path = _resolve(cfg["catalogue_csv"])
    sha_before = file_sha256(cat_path)

    data = generate(cfg, catalogue)
    validate_dataset(data["customers"], data["orders"], data["order_items"], catalogue, cfg)

    out = Path(output_dir) if output_dir else _resolve(cfg["output_dir"])
    out.mkdir(parents=True, exist_ok=True)
    paths = {}
    for name in ("customers", "orders", "order_items", "customer_product_profiles"):
        paths[name] = out / f"{name}.csv"
        data[name].to_csv(paths[name], index=False, lineterminator="\n")
    paths["report"] = out / "synthetic_data_report.md"
    paths["report"].write_text(render_report(cfg, data, catalogue, sha_before), encoding="utf-8", newline="\n")

    if file_sha256(cat_path) != sha_before:
        raise SyntheticDataError("The processed catalogue was modified during generation!")

    if verbose:
        o, i = data["orders"], data["order_items"]
        print(f"Seed {cfg['seed']} | window {data['window'].start} → {data['window'].end}")
        print(f"Customers: {len(data['customers'])}  Orders: {len(o):,}  Order items: {len(i):,}  "
              f"Units: {int(i['quantity'].sum()):,}")
        print("Validation: OK (catalogue unchanged)")
        for p in paths.values():
            print(f"  wrote {p}")
    return {"data": data, "paths": paths, "config": cfg, "catalogue": catalogue}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Generate synthetic purchase history for the curated catalogue")
    ap.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    ap.add_argument("--output-dir", type=Path, help="Defaults to output_dir in the config (data/synthetic)")
    ap.add_argument("--seed", type=int, help="Override the config seed")
    ap.add_argument("--customers", type=int, help="Override n_customers (segment counts are rescaled)")
    args = ap.parse_args(argv)
    try:
        run(args.config, args.output_dir, args.seed, args.customers)
    except SyntheticDataError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
