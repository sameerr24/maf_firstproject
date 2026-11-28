from __future__ import annotations

import datetime as dt

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import SubscriptionsBase, in_
from database.models import Product

MOVEMENT_REASONS = ("RESTOCK", "ADJUST", "ORDER")


class InventoryMovement(SubscriptionsBase):
    """One stock change (migration 005): admin restock / adjust, or an approved delivery."""
    __tablename__ = "inventory_movements"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    product_id: Mapped[str] = mapped_column(String, ForeignKey(Product.__table__.c.product_id), nullable=False)
    delta: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(String, nullable=False)
    occurrence_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("delivery_occurrences.occurrence_id"))
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint("delta <> 0", name="inventory_movements_delta_check"),
        CheckConstraint(in_("reason", MOVEMENT_REASONS), name="inventory_movements_reason_check"),
        Index("ix_inventory_movements_product", "product_id", created_at.desc()),
    )
