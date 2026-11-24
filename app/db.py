"""Database session for the Flask app.

Reuses Sameer's engine factory (database.connection.make_engine), which handles the
Supabase pooler settings and password masking. One scoped session per request,
removed on app-context teardown.
"""

from __future__ import annotations

from flask import Flask
from sqlalchemy.orm import scoped_session, sessionmaker

from database.connection import make_engine

db_session: scoped_session = scoped_session(sessionmaker(expire_on_commit=False))


def init_db(app: Flask) -> None:
    engine = make_engine(app.config["DATABASE_URL"])
    db_session.configure(bind=engine)
    app.extensions["db_engine"] = engine

    @app.teardown_appcontext
    def _remove_session(exc: BaseException | None) -> None:
        if exc is not None:
            db_session.rollback()
        db_session.remove()
