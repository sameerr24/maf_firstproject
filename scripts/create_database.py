"""Create the project tables in PostgreSQL (non-destructive).

    python scripts/create_database.py              # create any missing tables
    python scripts/create_database.py --check      # only report what exists / would be created
    python scripts/create_database.py --create-db  # also CREATE DATABASE if it doesn't exist

Never drops tables or changes columns. The only change made to existing tables
is enabling row-level security (needed on Supabase; see enable_rls). If an existing
table's columns differ from the models, that's reported so you can decide what to do.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # run from anywhere

from sqlalchemy import inspect, text  # noqa: E402
from sqlalchemy.engine import Engine  # noqa: E402
from sqlalchemy.exc import SQLAlchemyError  # noqa: E402

from database.connection import (  # noqa: E402
    DatabaseConfigError, check_connection, get_database_url, make_engine, safe_url,
)
from database.models import LOAD_ORDER, Base  # noqa: E402


def ensure_database_exists(url) -> bool:
    """CREATE DATABASE <name> if missing (connects to the 'postgres' maintenance DB). Returns True if created."""
    admin = make_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            exists = conn.execute(text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": url.database}).first()
            if exists:
                return False
            quoted = admin.dialect.identifier_preparer.quote(url.database)
            conn.execute(text(f"CREATE DATABASE {quoted}"))
            return True
    finally:
        admin.dispose()


def schema_status(engine: Engine) -> dict:
    """Which model tables exist, and column differences for those that do."""
    insp = inspect(engine)
    status = {"existing": [], "missing": [], "column_mismatches": {}}
    for model in LOAD_ORDER:
        table = model.__table__
        if not insp.has_table(table.name):
            status["missing"].append(table.name)
            continue
        status["existing"].append(table.name)
        db_cols = {c["name"] for c in insp.get_columns(table.name)}
        model_cols = {c.name for c in table.columns}
        if db_cols != model_cols:
            status["column_mismatches"][table.name] = {
                "missing_in_db": sorted(model_cols - db_cols), "extra_in_db": sorted(db_cols - model_cols)}
    return status


def create_tables(engine: Engine) -> list[str]:
    """Create missing tables (checkfirst=True: existing tables are never dropped or altered)."""
    before = set(schema_status(engine)["missing"])
    Base.metadata.create_all(engine, checkfirst=True)
    return [m.__tablename__ for m in LOAD_ORDER if m.__tablename__ in before]


def rls_status(engine: Engine) -> dict[str, bool]:
    """Row-level-security flag of each existing project table in the current schema."""
    names = [m.__tablename__ for m in LOAD_ORDER]
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT c.relname, c.relrowsecurity FROM pg_class c "
            "WHERE c.relnamespace = current_schema()::regnamespace AND c.relname = ANY(:names)"),
            {"names": names}).all()
    return dict(rows)


def enable_rls(engine: Engine) -> list[str]:
    """Enable row-level security on every project table that doesn't have it yet.

    Why: on Supabase, tables in the exposed schema are reachable through the
    auto-generated REST API with the project's public 'anon' key. RLS with no
    policies blocks that API access. Our scripts connect as the table owner,
    which RLS does not restrict, so loading/validation are unaffected. On a
    plain local PostgreSQL this is harmless. Non-destructive and idempotent.
    """
    to_enable = [t for t, on in rls_status(engine).items() if not on]
    if to_enable:
        with engine.begin() as conn:
            for name in to_enable:
                conn.execute(text(f"ALTER TABLE {engine.dialect.identifier_preparer.quote(name)} "
                                  "ENABLE ROW LEVEL SECURITY"))
    return sorted(to_enable, key=[m.__tablename__ for m in LOAD_ORDER].index)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Create project tables in PostgreSQL (never drops anything).")
    ap.add_argument("--check", action="store_true", help="Report status only; make no changes")
    ap.add_argument("--create-db", action="store_true", help="Create the database named in DATABASE_URL if missing")
    args = ap.parse_args(argv)

    try:
        url = get_database_url()
        print(f"Target: {safe_url(url)}")
        if args.create_db and not args.check:
            created = ensure_database_exists(url)
            print(f"Database '{url.database}': {'created' if created else 'already exists'}")
        engine = make_engine(url)
        info = check_connection(engine)
        print(f"Connected: PostgreSQL {info['server_version']} | database={info['database']} "
              f"user={info['user']} schema={info['schema']}")

        status = schema_status(engine)
        for name in status["existing"]:
            print(f"  [exists ] {name}")
        for name in status["missing"]:
            print(f"  [missing] {name}")
        for name, diff in status["column_mismatches"].items():
            print(f"  WARNING: '{name}' exists but its columns differ from the model: {diff}. "
                  "It was NOT modified. Drop/migrate it manually if intended.")

        rls = rls_status(engine)
        off = [t for t in status["existing"] if not rls.get(t)]
        if off:
            print(f"  Row-level security is OFF for: {', '.join(off)}")

        if args.check:
            print("Check only: no changes made.")
            return 1 if status["column_mismatches"] else 0
        if not status["missing"]:
            print("All tables already exist. Nothing to do.")
        else:
            created = create_tables(engine)
            print(f"Created {len(created)} table(s): {', '.join(created)}")
        enabled = enable_rls(engine)
        print(f"Row-level security enabled on: {', '.join(enabled)}" if enabled
              else "Row-level security already enabled on all tables.")
        return 1 if status["column_mismatches"] else 0
    except DatabaseConfigError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    except SQLAlchemyError as e:
        print(f"ERROR: database operation failed: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
