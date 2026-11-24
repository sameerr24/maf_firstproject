from __future__ import annotations

import datetime as dt

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import SubscriptionsBase
from database.models import Product


class Inventory(SubscriptionsBase):
    __tablename__ = "inventory"

    product_id: Mapped[str] = mapped_column(String, ForeignKey(Product.__table__.c.product_id), primary_key=True)
    available_qty: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=False), nullable=False, server_default=func.now(), onupdate=func.now())

    product: Mapped[Product] = relationship(Product)

    __table_args__ = (CheckConstraint("available_qty >= 0", name="inventory_available_qty_check"),)
