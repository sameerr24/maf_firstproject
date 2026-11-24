from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import SubscriptionsBase, in_
from app.models.subscriptions import DeliveryOccurrence
from database.models import Customer

DIRECTIONS = ("OUTBOUND", "INBOUND")


class WhatsAppMessage(SubscriptionsBase):
    """Log of every WhatsApp message in/out. wa_message_id is UNIQUE for webhook dedupe."""
    __tablename__ = "whatsapp_messages"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    wa_message_id: Mapped[str | None] = mapped_column(String, unique=True)
    occurrence_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("delivery_occurrences.occurrence_id"))
    customer_id: Mapped[str | None] = mapped_column(String, ForeignKey(Customer.__table__.c.customer_id))
    direction: Mapped[str] = mapped_column(String, nullable=False)
    message_type: Mapped[str | None] = mapped_column(String)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=False), nullable=False, server_default=func.now())

    occurrence: Mapped[DeliveryOccurrence | None] = relationship(DeliveryOccurrence)
    customer: Mapped[Customer | None] = relationship(Customer)

    __table_args__ = (CheckConstraint(in_("direction", DIRECTIONS), name="whatsapp_messages_direction_check"),)
