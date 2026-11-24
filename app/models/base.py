"""Declarative base for Shubham's tables.

Deliberately a separate MetaData from database.models.Base: Sameer's tests assert his
metadata holds exactly his 5 tables. Foreign keys to his tables point at his Column
objects directly, so nothing is duplicated.

The schema is owned by the SQL migration (already applied on Supabase). Do not call
create_all() against the shared database.
"""

from __future__ import annotations

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase


def in_(col: str, values) -> str:
    return f"{col} IN ({', '.join(repr(v) for v in values)})"


class SubscriptionsBase(DeclarativeBase):
    metadata = MetaData()
