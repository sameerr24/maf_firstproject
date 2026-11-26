"""Database storage for refresh-token families and shared authentication rate limits."""

from __future__ import annotations

import datetime as dt
import hashlib
import secrets
import threading
import uuid

from sqlalchemy import (
    Column, DateTime, Index, Integer, MetaData, String, Table, delete, insert, select, update,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from database.models import Customer

metadata = MetaData()

refresh_tokens = Table(
    "customer_auth_refresh_tokens",
    metadata,
    Column("token_hash", String(64), primary_key=True),
    Column("customer_id", String(20), nullable=False),
    Column("family_id", String(36), nullable=False),
    Column("issued_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("revoked_at", DateTime(timezone=True)),
    Column("replaced_by_hash", String(64)),
)
Index("ix_customer_auth_refresh_family", refresh_tokens.c.family_id)
Index("ix_customer_auth_refresh_customer", refresh_tokens.c.customer_id)

rate_limits = Table(
    "customer_auth_rate_limits",
    metadata,
    Column("bucket_key", String(64), primary_key=True),
    Column("window_started_at", DateTime(timezone=True), nullable=False),
    Column("hits", Integer, nullable=False),
)


class InvalidRefreshToken(Exception):
    """The refresh token is expired, revoked or unknown."""


class RefreshTokenReuse(Exception):
    """A rotated refresh token was replayed; its token family has been revoked."""


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def new_customer_id() -> str:
    """Generate a collision-resistant ID compatible with the existing varchar(20) key."""
    return f"CUST-{uuid.uuid4().hex[:15].upper()}"


class AuthStore:
    def __init__(self, engine):
        self.engine = engine
        self._rate_limit_calls = 0
        self._cleanup_lock = threading.Lock()

    def find_customer_by_phone(self, phone_e164: str) -> str | None:
        with Session(self.engine) as session:
            return session.scalar(select(Customer.customer_id).where(Customer.phone_e164 == phone_e164))

    def find_customer_name(self, customer_id: str) -> str | None:
        with Session(self.engine) as session:
            return session.scalar(select(Customer.customer_name).where(Customer.customer_id == customer_id))

    def register_customer(
        self,
        *,
        phone_e164: str,
        customer_name: str | None,
        household_size: int,
        preferred_weekday: str,
        signup_date: dt.date,
    ) -> tuple[str, bool]:
        for _ in range(5):
            with Session(self.engine) as session:
                existing = session.scalar(select(Customer.customer_id).where(Customer.phone_e164 == phone_e164))
                if existing:
                    return existing, False
                customer_id = new_customer_id()
                session.add(Customer(
                    customer_id=customer_id,
                    signup_date=signup_date,
                    behaviour_segment="new",
                    household_size=household_size,
                    preferred_weekday=preferred_weekday,
                    customer_name=customer_name,
                    phone_e164=phone_e164,
                    whatsapp_opt_in=False,
                ))
                try:
                    session.commit()
                    return customer_id, True
                except IntegrityError:
                    session.rollback()
                    existing = session.scalar(select(Customer.customer_id).where(Customer.phone_e164 == phone_e164))
                    if existing:
                        return existing, False
        raise RuntimeError("Could not allocate a unique customer ID.")

    def create_refresh_token(self, customer_id: str, lifetime_seconds: int) -> tuple[str, dt.datetime]:
        raw_token = secrets.token_urlsafe(48)
        now = _utc_now()
        expires_at = now + dt.timedelta(seconds=lifetime_seconds)
        with self.engine.begin() as connection:
            connection.execute(insert(refresh_tokens).values(
                token_hash=_token_hash(raw_token),
                customer_id=customer_id,
                family_id=str(uuid.uuid4()),
                issued_at=now,
                expires_at=expires_at,
            ))
        return raw_token, expires_at

    def rotate_refresh_token(self, raw_token: str) -> tuple[str, str, dt.datetime]:
        token_hash = _token_hash(raw_token)
        now = _utc_now()
        outcome = "invalid"
        result: tuple[str, str, dt.datetime] | None = None
        with self.engine.begin() as connection:
            row = connection.execute(
                select(refresh_tokens).where(refresh_tokens.c.token_hash == token_hash).with_for_update()
            ).mappings().one_or_none()
            if row is not None and row["revoked_at"] is not None:
                connection.execute(
                    update(refresh_tokens)
                    .where(refresh_tokens.c.family_id == row["family_id"],
                           refresh_tokens.c.revoked_at.is_(None))
                    .values(revoked_at=now)
                )
                outcome = "reuse"
            elif row is not None and self._as_utc(row["expires_at"]) > now:
                replacement = secrets.token_urlsafe(48)
                expires_at = now + dt.timedelta(seconds=self._refresh_lifetime_seconds(row["expires_at"],
                                                                                       row["issued_at"]))
                replacement_hash = _token_hash(replacement)
                connection.execute(insert(refresh_tokens).values(
                    token_hash=replacement_hash,
                    customer_id=row["customer_id"],
                    family_id=row["family_id"],
                    issued_at=now,
                    expires_at=expires_at,
                ))
                connection.execute(
                    update(refresh_tokens).where(refresh_tokens.c.token_hash == token_hash)
                    .values(revoked_at=now, replaced_by_hash=replacement_hash)
                )
                result = row["customer_id"], replacement, expires_at
                outcome = "rotated"
            elif row is not None:
                connection.execute(
                    update(refresh_tokens)
                    .where(refresh_tokens.c.family_id == row["family_id"],
                           refresh_tokens.c.revoked_at.is_(None))
                    .values(revoked_at=now)
                )
        if outcome == "reuse":
            raise RefreshTokenReuse()
        if result is None:
            raise InvalidRefreshToken()
        return result

    @staticmethod
    def _refresh_lifetime_seconds(expires_at: dt.datetime, issued_at: dt.datetime) -> int:
        expires_at = AuthStore._as_utc(expires_at)
        issued_at = AuthStore._as_utc(issued_at)
        return max(1, int((expires_at - issued_at).total_seconds()))

    @staticmethod
    def _as_utc(value: dt.datetime) -> dt.datetime:
        return value.replace(tzinfo=dt.timezone.utc) if value.tzinfo is None else value.astimezone(dt.timezone.utc)

    def revoke_refresh_token(self, raw_token: str) -> None:
        with self.engine.begin() as connection:
            connection.execute(
                update(refresh_tokens)
                .where(refresh_tokens.c.token_hash == _token_hash(raw_token),
                       refresh_tokens.c.revoked_at.is_(None))
                .values(revoked_at=_utc_now())
            )

    def consume_rate_limit(self, bucket_key: str, *, limit: int, window_seconds: int) -> tuple[bool, int]:
        now = _utc_now()
        dialect = self.engine.dialect.name
        insert_for_dialect = pg_insert if dialect == "postgresql" else sqlite_insert
        with self.engine.begin() as connection:
            with self._cleanup_lock:
                self._rate_limit_calls += 1
                should_cleanup = self._rate_limit_calls % 100 == 0
            if should_cleanup:
                connection.execute(
                    delete(rate_limits).where(
                        rate_limits.c.window_started_at < now - dt.timedelta(days=1)
                    )
                )
            connection.execute(
                insert_for_dialect(rate_limits)
                .values(bucket_key=bucket_key, window_started_at=now, hits=0)
                .on_conflict_do_nothing(index_elements=[rate_limits.c.bucket_key])
            )
            row = connection.execute(
                select(rate_limits).where(rate_limits.c.bucket_key == bucket_key).with_for_update()
            ).mappings().one()
            window_started_at = row["window_started_at"]
            if window_started_at.tzinfo is None:
                window_started_at = window_started_at.replace(tzinfo=dt.timezone.utc)
            elapsed = (now - window_started_at).total_seconds()
            if elapsed >= window_seconds:
                hits = 1
                window_started_at = now
            else:
                hits = row["hits"] + 1
            connection.execute(
                update(rate_limits).where(rate_limits.c.bucket_key == bucket_key)
                .values(window_started_at=window_started_at, hits=hits)
            )
        retry_after = max(1, int(window_seconds - elapsed))
        return hits <= limit, retry_after


def rate_limit_key(category: str, client_address: str) -> str:
    return hashlib.sha256(f"{category}:{client_address}".encode("utf-8")).hexdigest()
