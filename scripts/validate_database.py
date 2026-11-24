"""Validate the PostgreSQL database against the schema and the source CSVs.

    python scripts/validate_database.py                     # console report
    python scripts/validate_database.py --report data/database_validation_report.md
    python scripts/validate_database.py --strict            # warnings also fail

Exit code: 0 = no FAIL (warnings allowed), 1 = at least one FAIL (or a warning
with --strict), 2 = could not connect / configuration error.
Read-only: runs SELECT queries only.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # run from anywhere

from sqlalchemy import inspect, select, text  # noqa: E402
from sqlalchemy.engine import Engine  # noqa: E402
from sqlalchemy.exc import SQLAlchemyError  # noqa: E402

from database.connection import DatabaseConfigError, check_connection, make_engine, safe_url  # noqa: E402
from database.models import LOAD_ORDER, csv_columns  # noqa: E402
from database.sources import SourceTable, SourceValidationError, load_sources  # noqa: E402

PASS, WARN, FAIL = "PASS", "WARNING", "FAIL"
CATEGORY_ORDER = ["structure", "counts", "keys", "required", "relationships", "values", "consistency", "content"]

# Rows that came from the CSVs. Orders placed by subscriptions (source='subscription')
# and their items are app data, so the CSV count/content comparisons skip them.
CSV_ROWS_WHERE = {
    "orders": "source = 'manual'",
    "order_items": "order_id IN (SELECT order_id FROM orders WHERE source = 'manual')",
}


def _csv_rows_filter(name: str) -> str:
    return f" WHERE {CSV_ROWS_WHERE[name]}" if name in CSV_ROWS_WHERE else ""


@dataclass
class Check:
    category: str
    name: str
    status: str
    detail: str = ""


def _scalar(conn, sql: str) -> int:
    return conn.execute(text(sql)).scalar_one()


def _count_check(conn, category, name, sql, fail_status=FAIL, ok_detail="0 found", bad_detail="{n:,} found"):
    n = _scalar(conn, sql)
    return Check(category, name, PASS if n == 0 else fail_status, ok_detail if n == 0 else bad_detail.format(n=n))


def run_checks(engine: Engine, sources: dict[str, SourceTable] | None) -> list[Check]:
    checks: list[Check] = []
    insp = inspect(engine)

    # 1. Structure --------------------------------------------------------------
    missing = [m.__tablename__ for m in LOAD_ORDER if not insp.has_table(m.__tablename__)]
    checks.append(Check("structure", "All 5 tables exist", FAIL if missing else PASS,
                        f"missing: {missing}" if missing else ", ".join(m.__tablename__ for m in LOAD_ORDER)))
    if missing:
        return checks
    for m in LOAD_ORDER:
        t = m.__table__
        db_cols = {c["name"] for c in insp.get_columns(t.name)}
        diff = {c.name for c in t.columns} ^ db_cols
        checks.append(Check("structure", f"{t.name}: columns match model", FAIL if diff else PASS,
                            f"differences: {sorted(diff)}" if diff else f"{len(db_cols)} columns"))
        expected = {c.name for c in t.constraints if c.name and not c.name.startswith("pk_")}
        db_cons = {c["name"] for c in insp.get_foreign_keys(t.name)} | \
                  {c["name"] for c in insp.get_unique_constraints(t.name)} | \
                  {c["name"] for c in insp.get_check_constraints(t.name)}
        absent = expected - db_cons
        checks.append(Check("structure", f"{t.name}: FK/unique/check constraints present", FAIL if absent else PASS,
                            f"missing: {sorted(absent)}" if absent else f"{len(expected)} constraints"))

    with engine.connect() as conn:
        rls = dict(conn.execute(text(
            "SELECT relname, relrowsecurity FROM pg_class WHERE relnamespace = current_schema()::regnamespace "
            "AND relname = ANY(:n)"), {"n": [m.__tablename__ for m in LOAD_ORDER]}).all())
        off = [m.__tablename__ for m in LOAD_ORDER if not rls.get(m.__tablename__)]
        checks.append(Check("structure", "row-level security enabled (blocks Supabase public API access)",
                            WARN if off else PASS,
                            f"OFF for {off}; run scripts/create_database.py" if off else "all 5 tables"))

        # 2. Row counts vs CSV ---------------------------------------------------
        for m in LOAD_ORDER:
            name = m.__tablename__
            db_n = _scalar(conn, f"SELECT count(*) FROM {name}{_csv_rows_filter(name)}")
            if sources is None:
                checks.append(Check("counts", f"{name}: row count", WARN, f"db={db_n:,}; CSV not available"))
            else:
                csv_n = len(sources[name].records)
                checks.append(Check("counts", f"{name}: rows match CSV", PASS if db_n == csv_n else FAIL,
                                    f"db={db_n:,} csv={csv_n:,}"))
            if db_n == 0:
                checks.append(Check("counts", f"{name}: has data", FAIL, "table is empty"))

        # 3. Keys and required fields ------------------------------------------
        for m in LOAD_ORDER:
            t = m.__table__
            pk = ", ".join(c.name for c in t.primary_key.columns)
            checks.append(_count_check(conn, "keys", f"{t.name}: primary key ({pk}) unique",
                                       f"SELECT count(*) FROM (SELECT {pk} FROM {t.name} GROUP BY {pk} HAVING count(*) > 1) d",
                                       bad_detail="{n:,} duplicated keys"))
            required = [c.name for c in t.columns if not c.nullable]
            nulls = " + ".join(f"count(*) FILTER (WHERE {c} IS NULL)" for c in required)
            checks.append(_count_check(conn, "required", f"{t.name}: required fields populated ({len(required)} cols)",
                                       f"SELECT {nulls} FROM {t.name}", bad_detail="{n:,} NULL values"))
            text_cols = [c.name for c in t.columns if not c.nullable and c.type.python_type is str]
            blanks = " + ".join(f"count(*) FILTER (WHERE btrim({c}) = '')" for c in text_cols)
            checks.append(_count_check(conn, "required", f"{t.name}: no blank text in required fields",
                                       f"SELECT {blanks} FROM {t.name}", fail_status=WARN,
                                       bad_detail="{n:,} blank values"))

        # 4. Referential integrity ----------------------------------------------
        orphan_sql = {
            "orders -> customers (orphaned orders)":
                "SELECT count(*) FROM orders o LEFT JOIN customers c USING (customer_id) WHERE c.customer_id IS NULL",
            "order_items -> orders (orphaned order items)":
                "SELECT count(*) FROM order_items i LEFT JOIN orders o USING (order_id) WHERE o.order_id IS NULL",
            "order_items -> products":
                "SELECT count(*) FROM order_items i LEFT JOIN products p USING (product_id) WHERE p.product_id IS NULL",
            "customer_product_profiles -> customers":
                "SELECT count(*) FROM customer_product_profiles f LEFT JOIN customers c USING (customer_id) WHERE c.customer_id IS NULL",
            "customer_product_profiles -> products":
                "SELECT count(*) FROM customer_product_profiles f LEFT JOIN products p USING (product_id) WHERE p.product_id IS NULL",
            "order_items.product_type matches products":
                "SELECT count(*) FROM order_items i JOIN products p USING (product_id) WHERE i.product_type <> p.product_type",
            "profiles.product_type matches products":
                "SELECT count(*) FROM customer_product_profiles f JOIN products p USING (product_id) WHERE f.product_type <> p.product_type",
        }
        for name, sql in orphan_sql.items():
            checks.append(_count_check(conn, "relationships", name, sql, bad_detail="{n:,} invalid references"))
        checks.append(_count_check(conn, "relationships", "duplicate profiles per (customer, product)",
            "SELECT count(*) FROM (SELECT 1 FROM customer_product_profiles GROUP BY customer_id, product_id HAVING count(*) > 1) d"))
        checks.append(_count_check(conn, "relationships", "duplicate product within one order",
            "SELECT count(*) FROM (SELECT 1 FROM order_items GROUP BY order_id, product_id HAVING count(*) > 1) d"))
        checks.append(_count_check(conn, "relationships", "orders without items",
            "SELECT count(*) FROM orders o WHERE NOT EXISTS (SELECT 1 FROM order_items i WHERE i.order_id = o.order_id)",
            fail_status=WARN))
        checks.append(_count_check(conn, "relationships", "customers without orders",
            "SELECT count(*) FROM customers c WHERE NOT EXISTS (SELECT 1 FROM orders o WHERE o.customer_id = c.customer_id)",
            fail_status=WARN, bad_detail="{n:,} customers (possible for brand-new customers)"))

        # 5. Numeric validity and derived values --------------------------------
        numeric_sql = {
            "products: price > 0 and 0 < discount_price <= price":
                "SELECT count(*) FROM products WHERE price <= 0 OR discount_price <= 0 OR discount_price > price",
            "order_items: quantity > 0": "SELECT count(*) FROM order_items WHERE quantity <= 0",
            "order_items: line_total = quantity * unit_price":
                "SELECT count(*) FROM order_items WHERE line_total <> quantity * unit_price",
            "orders: order_total = sum(line_total)":
                "SELECT count(*) FROM orders o JOIN (SELECT order_id, sum(line_total) s FROM order_items GROUP BY order_id) i "
                "USING (order_id) WHERE o.order_total <> i.s",
            "orders: item_count / unit_count match items":
                "SELECT count(*) FROM orders o JOIN (SELECT order_id, count(*) n, sum(quantity) q FROM order_items "
                "GROUP BY order_id) i USING (order_id) WHERE o.item_count <> i.n OR o.unit_count <> i.q",
            "orders: order_date = date(order_datetime)":
                "SELECT count(*) FROM orders WHERE order_datetime::date <> order_date",
            "profiles: intervals/quantities positive, active_from <= active_until":
                "SELECT count(*) FROM customer_product_profiles WHERE intended_interval_days <= 0 "
                "OR intended_mean_quantity <= 0 OR active_from > active_until OR purchase_occasions < 0",
        }
        for name, sql in numeric_sql.items():
            checks.append(_count_check(conn, "values", name, sql, bad_detail="{n:,} rows"))

        # 6. Consistency warnings (plausible in real data, unexpected here) ----
        warn_sql = {
            "orders dated before customer signup":
                "SELECT count(*) FROM orders o JOIN customers c USING (customer_id) WHERE o.order_date < c.signup_date",
            "order_items.unit_price differs from catalogue discount_price":
                "SELECT count(*) FROM order_items i JOIN products p USING (product_id) WHERE i.unit_price <> p.discount_price",
            "purchased (customer, product) pairs without a profile":
                "SELECT count(*) FROM (SELECT DISTINCT o.customer_id, i.product_id FROM order_items i JOIN orders o USING (order_id)) x "
                "LEFT JOIN customer_product_profiles f USING (customer_id, product_id) WHERE f.customer_id IS NULL",
            "profiles.purchase_occasions differs from actual order_items":
                "SELECT count(*) FROM customer_product_profiles f LEFT JOIN (SELECT o.customer_id, i.product_id, count(*) n "
                "FROM order_items i JOIN orders o USING (order_id) GROUP BY 1, 2) a USING (customer_id, product_id) "
                "WHERE f.purchase_occasions <> coalesce(a.n, 0)",
        }
        for name, sql in warn_sql.items():
            checks.append(_count_check(conn, "consistency", name, sql, fail_status=WARN, bad_detail="{n:,} rows"))
        zero = _scalar(conn, "SELECT count(*) FROM customer_product_profiles WHERE purchase_occasions = 0")
        checks.append(Check("consistency", "profiles with zero purchases", PASS,
                            f"{zero} (expected: product assigned but first purchase falls after the history window)"))

        # 7. Content identical to the CSVs --------------------------------------
        if sources is not None:
            for m in LOAD_ORDER:
                t = m.__table__
                cols = [c.name for c in csv_columns(t)]
                pk = [c.name for c in t.primary_key.columns]
                query = select(*[t.c[c] for c in cols])
                if t.name in CSV_ROWS_WHERE:
                    query = query.where(text(CSV_ROWS_WHERE[t.name]))
                rows = conn.execute(query).all()
                db_rows = {tuple(getattr(r, c) for c in pk): tuple(r) for r in rows}
                src_rows = {tuple(rec[c] for c in pk): tuple(rec[c] for c in cols) for rec in sources[t.name].records}
                missing_in_db = src_rows.keys() - db_rows.keys()
                extra_in_db = db_rows.keys() - src_rows.keys()
                differing = [k for k in src_rows.keys() & db_rows.keys() if src_rows[k] != db_rows[k]]
                bad = len(missing_in_db) + len(extra_in_db) + len(differing)
                detail = (f"{len(src_rows):,} rows identical" if not bad else
                          f"missing={len(missing_in_db)} extra={len(extra_in_db)} differing={len(differing)}"
                          + (f", e.g. {sorted(differing, key=str)[:3]}" if differing else ""))
                checks.append(Check("content", f"{t.name}: values identical to CSV", FAIL if bad else PASS, detail))
    return checks


def render(checks: list[Check], header: str, markdown: bool = False) -> str:
    counts = {s: sum(c.status == s for c in checks) for s in (PASS, WARN, FAIL)}
    overall = FAIL if counts[FAIL] else (WARN if counts[WARN] else PASS)
    order = list(dict.fromkeys(CATEGORY_ORDER + [c.category for c in checks]))
    checks = sorted(checks, key=lambda c: order.index(c.category))
    if markdown:
        lines = ["# Database Validation Report", "", header, "",
                 f"**Overall: {overall}**: {counts[PASS]} passed, {counts[WARN]} warnings, {counts[FAIL]} failed", "",
                 "| Status | Category | Check | Detail |", "|---|---|---|---|"]
        lines += [f"| {c.status} | {c.category} | {c.name} | {c.detail} |" for c in checks]
        return "\n".join(lines) + "\n"
    icon = {PASS: "[PASS]   ", WARN: "[WARNING]", FAIL: "[FAIL]   "}
    lines, cat = [header, ""], None
    for c in checks:
        if c.category != cat:
            cat = c.category
            lines.append(f"-- {cat} " + "-" * (60 - len(cat)))
        lines.append(f"{icon[c.status]} {c.name}" + (f"  ({c.detail})" if c.detail else ""))
    lines += ["", f"Overall: {overall}: {counts[PASS]} passed, {counts[WARN]} warnings, {counts[FAIL]} failed"]
    return "\n".join(lines)


def exit_code(checks: list[Check], strict: bool = False) -> int:
    if any(c.status == FAIL for c in checks):
        return 1
    if strict and any(c.status == WARN for c in checks):
        return 1
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Validate the PostgreSQL database (read-only).")
    ap.add_argument("--report", type=Path, help="Also write a Markdown report to this path")
    ap.add_argument("--strict", action="store_true", help="Treat warnings as failures")
    args = ap.parse_args(argv)
    try:
        sources = load_sources()
    except SourceValidationError as e:
        print(f"WARNING: source CSVs failed validation; skipping CSV comparisons.\n{e}", file=sys.stderr)
        sources = None
    try:
        engine = make_engine()
        info = check_connection(engine)
        header = (f"Database: {safe_url(engine.url)} (PostgreSQL {info['server_version']}, "
                  f"schema {info['schema']})")
        checks = run_checks(engine, sources)
    except DatabaseConfigError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    except SQLAlchemyError as e:
        print(f"ERROR: validation query failed: {e}", file=sys.stderr)
        return 2
    print(render(checks, header))
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(render(checks, header, markdown=True), encoding="utf-8", newline="\n")
        print(f"\nReport written to {args.report}")
    return exit_code(checks, args.strict)


if __name__ == "__main__":
    sys.exit(main())
