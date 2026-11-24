"""Read and validate the five source CSVs against the ORM schema.

Validation happens entirely in Python *before* the database is touched, so bad
data never reaches PostgreSQL. Conversion is strict: nothing is silently coerced,
rounded, or dropped. Every problem is collected and reported together.
"""

from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pandas as pd
from sqlalchemy import BigInteger, Boolean, Date, DateTime, Integer, Numeric, SmallInteger, String, Text
from sqlalchemy.orm import DeclarativeBase

from database.connection import PROJECT_ROOT
from database.models import LOAD_ORDER, Customer, CustomerProductProfile, Order, OrderItem, Product, csv_columns

SOURCE_FILES: dict[type[DeclarativeBase], str] = {
    Product: "data/processed/selected_products.csv",
    Customer: "data/synthetic/customers.csv",
    Order: "data/synthetic/orders.csv",
    OrderItem: "data/synthetic/order_items.csv",
    CustomerProductProfile: "data/synthetic/customer_product_profiles.csv",
}
MAX_EXAMPLES = 5


class SourceValidationError(Exception):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("Source data validation failed:\n  - " + "\n  - ".join(errors))


@dataclass
class SourceTable:
    model: type[DeclarativeBase]
    path: Path
    sha256: str
    records: list[dict] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.model.__tablename__


# --------------------------------------------------------------------------- #
# Value conversion (strict)
# --------------------------------------------------------------------------- #
_TRUE, _FALSE = {"true", "t", "1"}, {"false", "f", "0"}


def convert_value(raw: str, col):
    """Convert one CSV cell to the Python type of ``col``. Empty string -> None."""
    if raw == "":
        return None
    t = col.type
    if isinstance(t, Boolean):
        v = raw.strip().lower()
        if v in _TRUE:
            return True
        if v in _FALSE:
            return False
        raise ValueError(f"not a boolean: {raw!r}")
    if isinstance(t, (Integer, BigInteger, SmallInteger)):
        d = Decimal(raw)  # accepts "5" and "5.0" but rejects "5.5" below
        if d != d.to_integral_value():
            raise ValueError(f"not an integer: {raw!r}")
        return int(d)
    if isinstance(t, Numeric):
        try:
            d = Decimal(raw.strip())
        except InvalidOperation:
            raise ValueError(f"not a number: {raw!r}") from None
        if not d.is_finite():
            raise ValueError(f"not a finite number: {raw!r}")
        if t.scale is not None and d != d.quantize(Decimal(1).scaleb(-t.scale)):
            raise ValueError(f"{raw!r} has more than {t.scale} decimal places")
        if t.precision is not None and abs(d) >= Decimal(10) ** (t.precision - (t.scale or 0)):
            raise ValueError(f"{raw!r} exceeds NUMERIC({t.precision},{t.scale})")
        return d
    if isinstance(t, DateTime):
        return dt.datetime.fromisoformat(raw)
    if isinstance(t, Date):
        if len(raw) != 10:
            raise ValueError(f"not a YYYY-MM-DD date: {raw!r}")
        return dt.date.fromisoformat(raw)
    if isinstance(t, (String, Text)):
        if getattr(t, "length", None) and len(raw) > t.length:
            raise ValueError(f"longer than {t.length} characters")
        return raw
    raise TypeError(f"Unsupported column type {t!r} for {col.name}")


# --------------------------------------------------------------------------- #
# Reading
# --------------------------------------------------------------------------- #
def file_sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def source_paths(root: Path = PROJECT_ROOT, overrides: dict | None = None) -> dict:
    overrides = overrides or {}
    return {m: Path(overrides.get(m.__tablename__, root / rel)) for m, rel in SOURCE_FILES.items()}


def _examples(items) -> str:
    items = list(items)
    more = f" (+{len(items) - MAX_EXAMPLES} more)" if len(items) > MAX_EXAMPLES else ""
    return ", ".join(map(str, items[:MAX_EXAMPLES])) + more


def read_table(model, path: Path, errors: list[str]) -> SourceTable | None:
    name = model.__tablename__
    if not path.exists():
        errors.append(f"{name}: source file not found: {path}")
        return None
    sha = file_sha256(path)
    try:
        df = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8")
    except Exception as e:  # malformed CSV, bad encoding, ...
        errors.append(f"{name}: could not read {path.name}: {e}")
        return None

    expected = [c.name for c in csv_columns(model.__table__)]  # app-managed columns aren't in the CSV
    missing = [c for c in expected if c not in df.columns]
    extra = [c for c in df.columns if c not in expected]
    if missing:
        errors.append(f"{name}: {path.name} is missing required column(s): {missing}")
    if extra:
        errors.append(f"{name}: {path.name} has unexpected column(s) that the schema would drop: {extra}")
    if missing or extra:
        return None
    if df.empty:
        errors.append(f"{name}: {path.name} has no rows")
        return None

    cols = csv_columns(model.__table__)
    records, bad = [], {}
    for i, row in enumerate(df.itertuples(index=False), start=2):  # line 1 is the header
        rec = {}
        for col, raw in zip(cols, (getattr(row, c.name) for c in cols)):
            try:
                val = convert_value(raw, col)
            except (ValueError, TypeError) as e:
                bad.setdefault(f"{col.name}: {type(e).__name__}", []).append(f"line {i} ({e})")
                val = None
            else:
                if val is None and not col.nullable:
                    bad.setdefault(f"{col.name}: required value is empty", []).append(f"line {i}")
            rec[col.name] = val
        records.append(rec)
    for problem, where in bad.items():
        errors.append(f"{name}.{problem} at {_examples(where)}")
    return SourceTable(model, path, sha, records)


# --------------------------------------------------------------------------- #
# Cross-row / cross-table validation
# --------------------------------------------------------------------------- #
def _keys(records, cols):
    return [tuple(r[c] for c in cols) for r in records]


def check_uniqueness(t: SourceTable, errors: list[str]) -> None:
    table = t.model.__table__
    groups = [("primary key", [c.name for c in table.primary_key.columns])]
    for con in table.constraints:
        if con.__class__.__name__ == "UniqueConstraint":
            groups.append((f"unique {con.name}", [c.name for c in con.columns]))
    for col in table.columns:
        if col.unique:
            groups.append((f"unique {col.name}", [col.name]))
    csv_names = {c.name for c in csv_columns(table)}
    for label, cols in groups:
        if not set(cols) <= csv_names:  # e.g. unique phone_e164: app-managed, not in the CSV
            continue
        seen, dups = set(), []
        for k in _keys(t.records, cols):
            if k in seen:
                dups.append(k if len(k) > 1 else k[0])
            seen.add(k)
        if dups:
            errors.append(f"{t.name}: duplicate {label} ({', '.join(cols)}): {_examples(dups)}")


def check_foreign_keys(tables: dict[str, SourceTable], errors: list[str]) -> None:
    for t in tables.values():
        for fk in t.model.__table__.foreign_key_constraints:
            parent = fk.referred_table.name
            if parent not in tables:
                continue
            local = [c.name for c in fk.columns]
            remote = [e.column.name for e in fk.elements]
            valid = set(_keys(tables[parent].records, remote))
            orphans = sorted({k for k in _keys(t.records, local) if None not in k and k not in valid}, key=str)
            if orphans:
                errors.append(f"{t.name}: {len(orphans)} value(s) of ({', '.join(local)}) not found in "
                              f"{parent}({', '.join(remote)}): {_examples(orphans)}")


def check_business_rules(tables: dict[str, SourceTable], errors: list[str]) -> None:
    """Python mirror of the CHECK constraints, for clear messages before loading."""
    def rule(table, desc, pred, key):
        if table not in tables:
            return
        bad = [r[key] for r in tables[table].records if not _safe(pred, r)]
        if bad:
            errors.append(f"{table}: {len(bad)} row(s) violate '{desc}': {_examples(bad)}")

    rule("products", "price > 0", lambda r: r["price"] > 0, "product_id")
    rule("products", "0 < discount_price <= price", lambda r: 0 < r["discount_price"] <= r["price"], "product_id")
    rule("customers", "household_size > 0", lambda r: r["household_size"] > 0, "customer_id")
    rule("orders", "item_count > 0", lambda r: r["item_count"] > 0, "order_id")
    rule("orders", "unit_count >= item_count", lambda r: r["unit_count"] >= r["item_count"], "order_id")
    rule("orders", "order_total >= 0", lambda r: r["order_total"] >= 0, "order_id")
    rule("orders", "order_date = date(order_datetime)", lambda r: r["order_datetime"].date() == r["order_date"], "order_id")
    rule("order_items", "quantity > 0", lambda r: r["quantity"] > 0, "order_item_id")
    rule("order_items", "unit_price >= 0", lambda r: r["unit_price"] >= 0, "order_item_id")
    rule("order_items", "line_total = quantity * unit_price",
         lambda r: r["line_total"] == r["quantity"] * r["unit_price"], "order_item_id")
    rule("customer_product_profiles", "active_from <= active_until",
         lambda r: r["active_from"] <= r["active_until"], "customer_id")
    rule("customer_product_profiles", "intended_interval_days > 0", lambda r: r["intended_interval_days"] > 0, "customer_id")
    rule("customer_product_profiles", "purchase_occasions >= 0", lambda r: r["purchase_occasions"] >= 0, "customer_id")


def _safe(pred, r) -> bool:
    try:
        return bool(pred(r))
    except TypeError:  # a None from a failed conversion; already reported
        return True


def load_sources(root: Path = PROJECT_ROOT, overrides: dict | None = None) -> dict[str, SourceTable]:
    """Read all five CSVs and validate them. Raises SourceValidationError listing every problem."""
    errors: list[str] = []
    tables: dict[str, SourceTable] = {}
    for model, path in source_paths(root, overrides).items():
        t = read_table(model, path, errors)
        if t is not None:
            tables[t.name] = t
    for t in tables.values():
        check_uniqueness(t, errors)
    check_foreign_keys(tables, errors)
    check_business_rules(tables, errors)
    if errors:
        raise SourceValidationError(errors)
    return {m.__tablename__: tables[m.__tablename__] for m in LOAD_ORDER}
