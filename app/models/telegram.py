from __future__ import annotations

import datetime as dt

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import SubscriptionsBase
from database.models import Customer


class TelegramLink(SubscriptionsBase):
    """Customer <-> Telegram chat (migrations/sql/002_telegram_links.sql). Created when the
    customer shares their phone number with the bot and it matches customers.phone_e164."""
    __tablename__ = "telegram_links"

    customer_id: Mapped[str] = mapped_column(String, ForeignKey(Customer.__table__.c.customer_id), primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    linked_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
