"""SQLAlchemy engine/session setup for PostgreSQL.

Credentials come only from environment variables (optionally loaded from the
project-root ``.env`` file). Nothing is hardcoded.

    DATABASE_URL       main development database (used by scripts)
    TEST_DATABASE_URL  separate database used by integration tests only
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, Engine, make_url
from sqlalchemy.exc import ArgumentError, OperationalError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = PROJECT_ROOT / ".env"


class DatabaseConfigError(RuntimeError):
    """Missing/invalid configuration or an unreachable database."""


def load_env() -> None:
    """Load ``.env`` from the project root. Real environment variables win."""
    load_dotenv(ENV_FILE, override=False)


def get_database_url(env_var: str = "DATABASE_URL") -> URL:
    load_env()
    raw = os.getenv(env_var, "").strip()
    if not raw:
        raise DatabaseConfigError(
            f"{env_var} is not set. Copy .env.example to .env and set {env_var}, e.g.\n"
            f"  {env_var}=postgresql+psycopg://<user>:<password>@localhost:5432/recommendation_db"
        )
    try:
        url = make_url(raw)
    except ArgumentError as e:
        raise DatabaseConfigError(f"{env_var} is not a valid SQLAlchemy URL: {e}") from None
    if url.get_backend_name() != "postgresql":
        raise DatabaseConfigError(f"{env_var} must point to PostgreSQL (got '{url.get_backend_name()}').")
    if not url.database:
        raise DatabaseConfigError(f"{env_var} does not name a database.")
    return url


def safe_url(url: URL | str) -> str:
    """URL with the password masked, for log messages."""
    return make_url(url).render_as_string(hide_password=True)


def make_engine(url: URL | str | None = None, *, search_path: str | None = None, **kwargs) -> Engine:
    """Create an engine with a small per-process pool for the shared Supabase database.

    ``search_path`` pins a PostgreSQL schema (used for test isolation). Callers can
    override pool sizing through kwargs when a dedicated database needs more capacity.
    """
    url = make_url(url) if url is not None else get_database_url()
    kwargs.setdefault("pool_size", 1)
    kwargs.setdefault("max_overflow", 0)
    connect_args = kwargs.pop("connect_args", {})
    connect_args.setdefault("connect_timeout", 10)
    connect_args.setdefault("application_name", "smart-reorder-recsys")
    if url.port == 6543:
        # Supabase/PgBouncer *transaction* pooler: server-side prepared statements
        # don't survive between transactions, so turn psycopg's auto-prepare off.
        connect_args.setdefault("prepare_threshold", None)
    if search_path:
        connect_args["options"] = f"-csearch_path={search_path}"
    return create_engine(url, pool_pre_ping=True, future=True, connect_args=connect_args, **kwargs)


def check_connection(engine: Engine) -> dict:
    """Run a trivial query; return server info or raise DatabaseConfigError with a clear message."""
    try:
        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT current_database(), current_user, current_setting('server_version'), current_schema()"
            )).one()
    except OperationalError as e:
        raise DatabaseConfigError(
            f"Could not connect to {safe_url(engine.url)}.\n"
            f"  Is PostgreSQL running, does the database exist, and are the credentials right?\n"
            f"  Driver said: {str(e.orig).strip().splitlines()[0] if e.orig else e}"
        ) from None
    except SQLAlchemyError as e:
        raise DatabaseConfigError(f"Database error while connecting to {safe_url(engine.url)}: {e}") from None
    return {"database": row[0], "user": row[1], "server_version": row[2], "schema": row[3]}


_SessionFactory: sessionmaker | None = None


def get_sessionmaker(engine: Engine | None = None) -> sessionmaker:
    global _SessionFactory
    if engine is not None:
        return sessionmaker(bind=engine, expire_on_commit=False)
    if _SessionFactory is None:
        _SessionFactory = sessionmaker(bind=make_engine(), expire_on_commit=False)
    return _SessionFactory


@contextmanager
def session_scope(engine: Engine | None = None) -> Iterator[Session]:
    """Transactional scope: commit on success, roll back on any exception."""
    session = get_sessionmaker(engine)()
    try:
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()
