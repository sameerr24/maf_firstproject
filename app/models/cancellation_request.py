from __future__ import annotations

import datetime as dt

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, String, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import SubscriptionsBase, in_

CANCEL_REQUEST_STATUSES = ("PENDING", "CONFIRMED", "DECLINED", "WITHDRAWN")
CANCEL_REQUEST_CHANNELS = ("telegram", "dashboard", "admin")


class CancellationRequest(SubscriptionsBase):
    """The admin asked the customer to confirm cancelling a subscription (migration 007).
    PENDING until the customer confirms or declines (Telegram or dashboard) or the admin
    withdraws it. At most one PENDING per subscription."""
    __tablename__ = "cancellation_requests"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    subscription_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("subscriptions.subscription_id"), nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, server_default="PENDING", default="PENDING")
    requested_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                      server_default=func.now())
    responded_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    channel: Mapped[str | None] = mapped_column(String)

    subscription = relationship("Subscription")

    __table_args__ = (
        CheckConstraint(in_("status", CANCEL_REQUEST_STATUSES), name="cancellation_requests_status_check"),
        CheckConstraint(f"channel IS NULL OR {in_('channel', CANCEL_REQUEST_CHANNELS)}",
                        name="cancellation_requests_channel_check"),
        Index("ux_cancellation_requests_one_pending", "subscription_id", unique=True,
              postgresql_where=text("status = 'PENDING'")),
        Index("ix_cancellation_requests_subscription", "subscription_id", requested_at.desc()),
    )
