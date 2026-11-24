"""Load the five project CSVs into PostgreSQL in one transaction.

    python scripts/load_data.py              # load into EMPTY tables (refuses if data exists)
    python scripts/load_data.py --replace    # transactional full refresh of all five tables
    python scripts/load_data.py --dry-run    # validate the CSVs only; don't touch the database

Import strategy: transactional full refresh.
  1. Every CSV is read and validated in Python first (columns, types, keys, FKs,
     business rules). Any problem aborts before the database is touched.
  2. One transaction: lock the five tables, check existing row counts, and if any
     data exists, continue only with --replace (deleting in reverse dependency order).
     Then insert products -> customers -> orders -> order_items -> profiles.
  3. Row counts are verified inside the transaction, and the source files are
     re-hashed to prove they were not modified. Only then is it committed.
  Any error rolls back everything, so the database is never left partially loaded.
Re-running with --replace always produces the same database state (idempotent).
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # run from anywhere

from sqlalchemy import delete, func, insert, inspect, or_, select, text  # noqa: E402
from sqlalchemy.engine import Engine  # noqa: E402
from sqlalchemy.exc import IntegrityError, SQLAlchemyError  # noqa: E402

from database.connection import DatabaseConfigError, check_connection, make_engine, safe_url  # noqa: E402
from database.models import LOAD_ORDER, app_columns  # noqa: E402
from database.sources import SourceTable, SourceValidationError, file_sha256, load_sources  # noqa: E402

BATCH_SIZE = 5000


class LoadError(RuntimeError):
    """Load aborted; the transaction was rolled back and the database is unchanged."""


def existing_counts(conn) -> dict[str, int]:
    return {m.__tablename__: conn.execute(select(func.count()).select_from(m.__table__)).scalar_one()
            for m in LOAD_ORDER}


def app_data_in_use(conn) -> dict[str, int]:
    """Rows whose app-managed columns (customer names/phones, subscription orders, ...) hold data.

    --replace deletes and re-inserts from the CSVs, which would silently wipe that data,
    so the load refuses when any is found.
    """
    found = {}
    for model in LOAD_ORDER:
        t = model.__table__
        conds = []
        for c in app_columns(t):
            default = c.server_default.arg if c.server_default is not None else None
            conds.append(t.c[c.name].is_not(None) if default is None
                         else t.c[c.name].is_distinct_from(default))
        if conds:
            n = conn.execute(select(func.count()).select_from(t).where(or_(*conds))).scalar_one()
            if n:
                found[t.name] = n
    return found


def load_all(engine: Engine, sources: dict[str, SourceTable], replace: bool = False,
             log=print) -> dict[str, int]:
    """Load all sources in one transaction. Returns inserted row counts per table."""
    insp = inspect(engine)
    missing = [m.__tablename__ for m in LOAD_ORDER if not insp.has_table(m.__tablename__)]
    if missing:
        raise LoadError(f"Table(s) {missing} do not exist. Run: python scripts/create_database.py")

    inserted: dict[str, int] = {}
    try:
        with engine.begin() as conn:  # commits on success, rolls back on any exception
            conn.execute(text("SET LOCAL lock_timeout = '10s'"))
            names = ", ".join(m.__tablename__ for m in LOAD_ORDER)
            conn.execute(text(f"LOCK TABLE {names} IN ACCESS EXCLUSIVE MODE"))

            before = existing_counts(conn)
            if any(before.values()):
                summary = ", ".join(f"{k}={v:,}" for k, v in before.items())
                if not replace:
                    raise LoadError(
                        f"The database already contains data ({summary}). Nothing was changed.\n"
                        "  Re-run with --replace to perform a transactional full refresh "
                        "(existing rows in these five tables are replaced by the CSV contents).")
                in_use = app_data_in_use(conn)
                if in_use:
                    raise LoadError(
                        f"App-managed data would be wiped by --replace: {in_use} row(s) have customer "
                        "names/phones/WhatsApp opt-in or subscription orders/delivery slots. Nothing was changed.\n"
                        "  The shared database is now also written by the Flask app; don't --replace it. "
                        "Use a local database for reloads.")
                log(f"Replacing existing data ({summary}) inside the transaction...")
                for model in reversed(LOAD_ORDER):
                    conn.execute(delete(model.__table__))

            for model in LOAD_ORDER:
                src = sources[model.__tablename__]
                t0 = time.perf_counter()
                for i in range(0, len(src.records), BATCH_SIZE):
                    conn.execute(insert(model.__table__), src.records[i:i + BATCH_SIZE])
                inserted[src.name] = len(src.records)
                log(f"  inserted {len(src.records):>6,} rows into {src.name:<26} ({time.perf_counter() - t0:.2f}s)")

            after = existing_counts(conn)
            wrong = {k: (after[k], inserted[k]) for k in inserted if after[k] != inserted[k]}
            if wrong:
                raise LoadError(f"Row-count verification failed (db, csv): {wrong}")
            changed = [s.name for s in sources.values() if file_sha256(s.path) != s.sha256]
            if changed:
                raise LoadError(f"Source file(s) changed during the load: {changed}")
    except IntegrityError as e:
        raise LoadError(f"Database constraint rejected the data: {str(e.orig).strip()}") from None
    except SQLAlchemyError as e:
        raise LoadError(f"Database error during load: {e}") from None
    return inserted


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Load the project CSVs into PostgreSQL (single transaction).")
    ap.add_argument("--replace", action="store_true",
                    help="Allow a transactional full refresh when the tables already contain data")
    ap.add_argument("--dry-run", action="store_true", help="Validate the CSVs only; do not connect")
    args = ap.parse_args(argv)

    print("Validating source CSVs...")
    try:
        sources = load_sources()
    except SourceValidationError as e:
        print(f"ERROR: {e}\nNothing was loaded.", file=sys.stderr)
        return 1
    for s in sources.values():
        print(f"  OK {s.name:<26} {len(s.records):>6,} rows  <- {s.path.relative_to(Path.cwd()) if s.path.is_relative_to(Path.cwd()) else s.path}")
    if args.dry_run:
        print("Dry run: CSVs are valid. Database not touched.")
        return 0

    try:
        engine = make_engine()
        info = check_connection(engine)
        print(f"Connected to {safe_url(engine.url)} (PostgreSQL {info['server_version']})")
        print("Loading in one transaction...")
        counts = load_all(engine, sources, replace=args.replace)
    except (DatabaseConfigError, LoadError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        if isinstance(e, LoadError):
            print("The transaction was rolled back; the database is unchanged.", file=sys.stderr)
        return 1
    print(f"Committed. {sum(counts.values()):,} rows loaded across {len(counts)} tables. "
          "Run: python scripts/validate_database.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
