"""Turn raw order lines into purchase occasions.

Database facts this relies on (checked against the live schema):
  * one ``orders`` row = one checkout (transaction), with an ``order_date``;
  * ``order_items`` holds its lines, at most one line per product per order
    (``uq_order_items_order_product``), with an integer ``quantity`` of product packs.

Rules:
  1. Records for another customer, with an unparseable date, a date after the reference
     date, or a missing product are dropped (and counted in ``DataQualityReport``).
  2. Exact duplicate rows are dropped: by ``order_item_id`` when present, otherwise by the
     whole record.
  3. All lines of the same product in the same order become ONE occasion (quantities are
     added up), even if the uniqueness constraint above is ever relaxed.
  4. With ``merge_same_day=True`` (the default), orders of the same product on the same
     calendar day are merged into one occasion, so a forgotten-item second checkout does
     not create an artificial 0-day interval.
  5. Quantities must be positive whole numbers; others are ignored for quantity
     estimation, but the occasion still counts for timing.
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass, field
from typing import Any, Iterable

import pandas as pd

from recommendation_engine.data.purchase_data import PurchaseOccasion, PurchaseRecord


@dataclass
class DataQualityReport:
    """How many input rows were skipped or adjusted, and why."""
    records_in: int = 0
    other_customer: int = 0
    missing_product: int = 0
    invalid_date: int = 0
    after_reference_date: int = 0
    duplicate_records: int = 0
    invalid_quantity: int = 0
    orders_with_conflicting_dates: int = 0
    same_day_orders_merged: int = 0
    non_positive_intervals_dropped: int = 0
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        d = {k: v for k, v in self.__dict__.items() if k != "notes" and v}
        if self.notes:
            d["notes"] = list(self.notes)
        return d


def parse_date(value: Any) -> dt.date | None:
    """A date from a date/datetime/ISO string; None if it can't be parsed."""
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return dt.datetime.fromisoformat(value.strip()).date()
        except ValueError:
            return None
    return None


def parse_quantity(value: Any) -> int | None:
    """A positive whole number of units; None for missing, zero, negative, fractional or junk."""
    if value is None or isinstance(value, bool):
        return None
    try:
        q = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(q) or q <= 0 or q != int(q):
        return None
    return int(q)


def build_purchase_occasions(
    records: Iterable[PurchaseRecord],
    customer_id: str,
    reference_date: dt.date,
    *,
    merge_same_day: bool = True,
    report: DataQualityReport | None = None,
) -> dict[str, list[PurchaseOccasion]]:
    """Group a customer's order lines into chronologically sorted occasions per product."""
    report = report if report is not None else DataQualityReport()
    rows = []
    for r in records:
        report.records_in += 1
        if r.customer_id != customer_id:
            report.other_customer += 1
            continue
        if not r.product_id or not r.order_id:
            report.missing_product += 1
            continue
        day = parse_date(r.order_date)
        if day is None:
            report.invalid_date += 1
            continue
        if day > reference_date:
            report.after_reference_date += 1
            continue
        qty = parse_quantity(r.quantity)
        if qty is None:
            report.invalid_quantity += 1
        rows.append({"order_item_id": r.order_item_id, "order_id": str(r.order_id), "product_id": str(r.product_id),
                     "day": day, "qty": qty, "raw_qty": repr(r.quantity)})
    if not rows:
        return {}

    df = pd.DataFrame(rows)
    df["qty"] = pd.to_numeric(df["qty"], errors="coerce")  # None -> NaN

    # 2. duplicates
    before = len(df)
    has_id = df["order_item_id"].notna()
    with_id = df[has_id].drop_duplicates(subset=["order_item_id"])
    without_id = df[~has_id].drop_duplicates(subset=["order_id", "product_id", "day", "raw_qty"])
    df = pd.concat([with_id, without_id], ignore_index=True)
    report.duplicate_records += before - len(df)

    # 3. one occasion per (order, product)
    per_order = (df.groupby(["product_id", "order_id"], sort=False)
                   .agg(day=("day", "min"), n_days=("day", "nunique"), qty=("qty", lambda s: s.sum(min_count=1)))
                   .reset_index())
    report.orders_with_conflicting_dates += int((per_order["n_days"] > 1).sum())

    # 4. optionally merge same-day orders
    if merge_same_day:
        grouped = per_order.groupby(["product_id", "day"], sort=False)
        merged = grouped.agg(qty=("qty", lambda s: s.sum(min_count=1)),
                             order_ids=("order_id", lambda s: tuple(sorted(s)))).reset_index()
        report.same_day_orders_merged += len(per_order) - len(merged)
    else:
        merged = per_order.assign(order_ids=per_order["order_id"].map(lambda o: (o,)))

    occasions: dict[str, list[PurchaseOccasion]] = {}
    for row in merged.itertuples(index=False):
        qty = None if pd.isna(row.qty) else int(row.qty)
        occasions.setdefault(row.product_id, []).append(
            PurchaseOccasion(product_id=row.product_id, occasion_date=row.day, quantity=qty, order_ids=row.order_ids))
    for pid in occasions:
        occasions[pid].sort(key=lambda o: (o.occasion_date, o.order_ids))
    return occasions
