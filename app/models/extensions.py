"""Partial mappings of Sameer's customers/orders tables, covering only the columns added
by Shubham's migration.

Sameer's Customer/Order models must match the CSV headers exactly (his tests and loader
enforce it), so the new columns can't be added there for now. These classes map the same
tables with just the primary key + new columns. Use them to read or UPDATE those columns;
never INSERT through them (the other NOT NULL columns are missing).

TODO (with Sameer): move these columns into database/models.py and drop this file.
"""

from __future__ import annotations

from sqlalchemy import Boolean, CheckConstraint, String, false, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import SubscriptionsBase, in_

ORDER_SOURCES = ("manual", "subscription")
E164_REGEX = r"^\+[1-9][0-9]{7,14}$"


class CustomerContact(SubscriptionsBase):
    __tablename__ = "customers"

    customer_id: Mapped[str] = mapped_column(String(20), primary_key=True)
    customer_name: Mapped[str | None] = mapped_column(String)
    phone_e164: Mapped[str | None] = mapped_column(String, unique=True)
    whatsapp_opt_in: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=false())

    __table_args__ = (
        CheckConstraint(f"phone_e164 IS NULL OR phone_e164 ~ '{E164_REGEX}'", name="customers_phone_e164_check"),
    )


class OrderMeta(SubscriptionsBase):
    __tablename__ = "orders"

    order_id: Mapped[str] = mapped_column(String(20), primary_key=True)
    source: Mapped[str] = mapped_column(String, nullable=False, server_default=text("'manual'"))
    delivery_slot: Mapped[str | None] = mapped_column(String)

    __table_args__ = (CheckConstraint(in_("source", ORDER_SOURCES), name="orders_source_check"),)
