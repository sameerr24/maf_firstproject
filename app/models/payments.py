from __future__ import annotations

import datetime as dt

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import SubscriptionsBase, in_
from database.models import Customer

AUTOPAY_MANDATE_STATUSES = ("PENDING", "ACTIVE", "FAILED", "CANCELLED")


class CustomerAutopayMandate(SubscriptionsBase):
    """The customer's shared Razorpay UPI mandate and its current setup attempt."""

    __tablename__ = "customer_autopay_mandates"

    customer_id: Mapped[str] = mapped_column(
        String, ForeignKey(Customer.__table__.c.customer_id, ondelete="CASCADE"), primary_key=True)
    setup_id: Mapped[str | None] = mapped_column(String(32), unique=True)
    razorpay_customer_id: Mapped[str | None] = mapped_column(String, unique=True)
    razorpay_order_id: Mapped[str | None] = mapped_column(String, unique=True)
    razorpay_payment_id: Mapped[str | None] = mapped_column(String, unique=True)
    razorpay_token_id: Mapped[str | None] = mapped_column(String, unique=True)
    status: Mapped[str] = mapped_column(String, nullable=False, server_default=text("'PENDING'"))
    max_amount_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    mandate_expires_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        CheckConstraint(in_("status", AUTOPAY_MANDATE_STATUSES), name="customer_autopay_status_check"),
        CheckConstraint("max_amount_paise > 0", name="customer_autopay_max_amount_check"),
        CheckConstraint(
            "status <> 'ACTIVE' OR "
            "(razorpay_customer_id IS NOT NULL AND razorpay_payment_id IS NOT NULL "
            "AND razorpay_token_id IS NOT NULL)",
            name="customer_autopay_active_details_check",
        ),
    )
