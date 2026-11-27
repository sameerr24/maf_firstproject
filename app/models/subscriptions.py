from __future__ import annotations

import datetime as dt
from decimal import Decimal

from sqlalchemy import (
    BigInteger, CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, Numeric, String,
    UniqueConstraint, func, text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import SubscriptionsBase, in_
from database.models import Customer, Order, Product

SUBSCRIPTION_STATUSES = ("ACTIVE", "PAUSED", "CANCELLED")
OCCURRENCE_STATUSES = (
    "SCHEDULED", "NOTIFIED", "APPROVED", "ORDER_PLACED", "SKIPPED", "DELAYED", "OUT_OF_STOCK", "CANCELLED",
    "STOCK_HOLD",       # short of stock at reminder time; waiting for a restock (migration 004)
)
SKIP_REASONS = ("USER_SKIP", "NO_RESPONSE", "OUT_OF_STOCK")


class Subscription(SubscriptionsBase):
    __tablename__ = "subscriptions"

    subscription_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    customer_id: Mapped[str] = mapped_column(String, ForeignKey(Customer.__table__.c.customer_id), nullable=False)
    product_id: Mapped[str] = mapped_column(String, ForeignKey(Product.__table__.c.product_id), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    frequency_days: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price_at_subscription: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, server_default=text("'ACTIVE'"))
    next_delivery_date: Mapped[dt.date | None] = mapped_column(Date)
    preferred_slot: Mapped[str | None] = mapped_column(String)
    paused_until: Mapped[dt.date | None] = mapped_column(Date)
    recommended_quantity: Mapped[int | None] = mapped_column(Integer)
    recommended_frequency_days: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    cancelled_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    customer: Mapped[Customer] = relationship(Customer)
    product: Mapped[Product] = relationship(Product)
    occurrences: Mapped[list[DeliveryOccurrence]] = relationship(
        back_populates="subscription", order_by="DeliveryOccurrence.scheduled_date")

    __table_args__ = (
        CheckConstraint("quantity > 0", name="subscriptions_quantity_check"),
        CheckConstraint("frequency_days > 0", name="subscriptions_frequency_days_check"),
        CheckConstraint("unit_price_at_subscription >= 0", name="subscriptions_unit_price_at_subscription_check"),
        CheckConstraint(in_("status", SUBSCRIPTION_STATUSES), name="subscriptions_status_check"),
        # One non-cancelled subscription per customer + product.
        Index("uq_live_subscription", "customer_id", "product_id", unique=True,
              postgresql_where=text("status <> 'CANCELLED'")),
        # Scheduler job A: active subscriptions coming due.
        Index("ix_subscriptions_due", "next_delivery_date", postgresql_where=text("status = 'ACTIVE'")),
    )


class DeliveryOccurrence(SubscriptionsBase):
    """One row per delivery cycle of a subscription."""
    __tablename__ = "delivery_occurrences"

    occurrence_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    subscription_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("subscriptions.subscription_id"), nullable=False)
    scheduled_date: Mapped[dt.date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, server_default=text("'SCHEDULED'"))
    skip_reason: Mapped[str | None] = mapped_column(String)
    delay_days: Mapped[int | None] = mapped_column(Integer)
    delivery_slot: Mapped[str | None] = mapped_column(String)
    unit_price_at_order: Mapped[Decimal | None] = mapped_column(Numeric)
    notified_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    response_deadline: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    hold_until: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))   # STOCK_HOLD expiry
    responded_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    order_id: Mapped[str | None] = mapped_column(String, ForeignKey(Order.__table__.c.order_id))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    subscription: Mapped[Subscription] = relationship(back_populates="occurrences")
    order: Mapped[Order | None] = relationship(Order)

    __table_args__ = (
        UniqueConstraint("subscription_id", "scheduled_date", name="uq_occurrence_per_cycle"),
        CheckConstraint(in_("status", OCCURRENCE_STATUSES), name="delivery_occurrences_status_check"),
        CheckConstraint(f"skip_reason IS NULL OR {in_('skip_reason', SKIP_REASONS)}",
                        name="delivery_occurrences_skip_reason_check"),
        CheckConstraint("delay_days IS NULL OR delay_days > 0", name="delivery_occurrences_delay_days_check"),
        # Scheduler job B: notified occurrences past their deadline.
        Index("ix_occurrences_pending_deadline", "response_deadline", postgresql_where=text("status = 'NOTIFIED'")),
        # Held deliveries (scheduler release/expiry, admin at-risk page).
        Index("ix_occurrences_stock_hold", "hold_until", postgresql_where=text("status = 'STOCK_HOLD'")),
    )
