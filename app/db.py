"""Database session for the Flask app.

Reuses Sameer's engine factory (database.connection.make_engine), which handles the
Supabase pooler settings and password masking. Each app owns its engine; db_session is
a scoped session bound to the current app's engine, removed on app-context teardown
(which rolls back anything not committed).
"""

from __future__ import annotations

from flask import Flask, current_app
from sqlalchemy.orm import Session, scoped_session

from database.connection import make_engine


def _new_session() -> Session:
    return Session(bind=current_app.extensions["db_engine"], expire_on_commit=False)


db_session: scoped_session = scoped_session(_new_session)


def init_db(app: Flask) -> None:
    # DB_SEARCH_PATH (optional) pins a PostgreSQL schema; tests use it for isolation.
    app.extensions["db_engine"] = make_engine(app.config["DATABASE_URL"],
                                              search_path=app.config.get("DB_SEARCH_PATH"))

    @app.teardown_appcontext
    def _remove_session(exc: BaseException | None) -> None:
        db_session.remove()
