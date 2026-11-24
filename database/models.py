"""SQLAlchemy 2.0 ORM models for the five project datasets.

Every column maps 1:1 to a column in the source CSVs (no invented attributes):

    products                   <- data/processed/selected_products.csv
    customers                  <- data/synthetic/customers.csv
    orders                     <- data/synthetic/orders.csv
    order_items                <- data/synthetic/order_items.csv
    customer_product_profiles  <- data/synthetic/customer_product_profiles.csv

Money uses NUMERIC (exact), dates use DATE, order_datetime uses TIMESTAMP
WITHOUT TIME ZONE (the source has no timezone).
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from sqlalchemy import (
    BigInteger, Boolean, CheckConstraint, Date, DateTime, ForeignKey, ForeignKeyConstraint,
    Index, Integer, MetaData, Numeric, SmallInteger, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
SELECTION_METHODS = ("auto", "manual_override", "fallback")


def _in(col: str, values) -> str:
    return f"{col} IN ({', '.join(repr(v) for v in values)})"


class Base(DeclarativeBase):
    # Deterministic constraint names (explicit names below take precedence).
    metadata = MetaData(naming_convention={
        "ix": "ix_%(table_name)s_%(column_0_N_name)s",
        "uq": "uq_%(table_name)s_%(column_0_N_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
    })


class Product(Base):
    __tablename__ = "products"

    product_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    sku_id: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True)
    product_type: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    product_name: Mapped[str] = mapped_column(Text, nullable=False)
    original_product_name: Mapped[str] = mapped_column(Text, nullable=False)
    brand: Mapped[str] = mapped_column(String(100), nullable=False)
    original_brand: Mapped[str] = mapped_column(String(100), nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    discount_price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    pack_size: Mapped[str] = mapped_column(String(50), nullable=False)
    pack_count: Mapped[int | None] = mapped_column(Integer)
    pack_unit_size: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))
    pack_unit: Mapped[str | None] = mapped_column(String(10))
    category: Mapped[str] = mapped_column(String(100), nullable=False)
    subcategory: Mapped[str] = mapped_column(String(100), nullable=False)
    image_url: Mapped[str] = mapped_column(Text, nullable=False)
    product_url: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    replenishable: Mapped[bool] = mapped_column(Boolean, nullable=False)
    consumption_pattern: Mapped[str | None] = mapped_column(String(50))
    selection_method: Mapped[str] = mapped_column(String(20), nullable=False)
    source_row_number: Mapped[int] = mapped_column(Integer, nullable=False)

    order_items: Mapped[list[OrderItem]] = relationship(back_populates="product", foreign_keys="OrderItem.product_id")
    profiles: Mapped[list[CustomerProductProfile]] = relationship(
        back_populates="product", foreign_keys="CustomerProductProfile.product_id")

    __table_args__ = (
        # Target for the composite FKs that keep the denormalised product_type consistent.
        UniqueConstraint("product_id", "product_type", name="uq_products_id_type"),
        CheckConstraint("price > 0", name="ck_products_price_positive"),
        CheckConstraint("discount_price > 0 AND discount_price <= price", name="ck_products_discount_price"),
        CheckConstraint("pack_count IS NULL OR pack_count > 0", name="ck_products_pack_count"),
        CheckConstraint("pack_unit_size IS NULL OR pack_unit_size > 0", name="ck_products_pack_unit_size"),
        CheckConstraint(_in("selection_method", SELECTION_METHODS), name="ck_products_selection_method"),
        CheckConstraint("source_row_number >= 0", name="ck_products_source_row"),
    )


class Customer(Base):
    __tablename__ = "customers"

    customer_id: Mapped[str] = mapped_column(String(20), primary_key=True)
    signup_date: Mapped[dt.date] = mapped_column(Date, nullable=False)
    behaviour_segment: Mapped[str] = mapped_column(String(30), nullable=False)
    household_size: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    preferred_weekday: Mapped[str] = mapped_column(String(3), nullable=False)

    orders: Mapped[list[Order]] = relationship(back_populates="customer")
    profiles: Mapped[list[CustomerProductProfile]] = relationship(back_populates="customer")

    __table_args__ = (
        CheckConstraint("household_size > 0", name="ck_customers_household_size"),
        CheckConstraint(_in("preferred_weekday", WEEKDAYS), name="ck_customers_weekday"),
    )


class Order(Base):
    __tablename__ = "orders"

    order_id: Mapped[str] = mapped_column(String(20), primary_key=True)
    customer_id: Mapped[str] = mapped_column(
        String(20), ForeignKey("customers.customer_id", ondelete="RESTRICT"), nullable=False)
    order_date: Mapped[dt.date] = mapped_column(Date, nullable=False)
    order_datetime: Mapped[dt.datetime] = mapped_column(DateTime(timezone=False), nullable=False)
    item_count: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_count: Mapped[int] = mapped_column(Integer, nullable=False)
    order_total: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)

    customer: Mapped[Customer] = relationship(back_populates="orders")
    items: Mapped[list[OrderItem]] = relationship(back_populates="order")

    __table_args__ = (
        CheckConstraint("item_count > 0", name="ck_orders_item_count"),
        CheckConstraint("unit_count >= item_count", name="ck_orders_unit_count"),
        CheckConstraint("order_total >= 0", name="ck_orders_total"),
        CheckConstraint("CAST(order_datetime AS DATE) = order_date", name="ck_orders_date_matches_datetime"),
        # Purchase-history lookups: a customer's orders in date order.
        Index("ix_orders_customer_date", "customer_id", "order_date"),
        Index("ix_orders_order_date", "order_date"),
    )


class OrderItem(Base):
    __tablename__ = "order_items"

    order_item_id: Mapped[str] = mapped_column(String(20), primary_key=True)
    order_id: Mapped[str] = mapped_column(
        String(20), ForeignKey("orders.order_id", ondelete="RESTRICT"), nullable=False)
    product_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("products.product_id", ondelete="RESTRICT"), nullable=False)
    product_type: Mapped[str] = mapped_column(String(50), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    line_total: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)

    order: Mapped[Order] = relationship(back_populates="items")
    product: Mapped[Product] = relationship(back_populates="order_items", foreign_keys=[product_id])

    __table_args__ = (
        UniqueConstraint("order_id", "product_id", name="uq_order_items_order_product"),
        ForeignKeyConstraint(["product_id", "product_type"], ["products.product_id", "products.product_type"],
                             name="fk_order_items_product_type", ondelete="RESTRICT"),
        CheckConstraint("quantity > 0", name="ck_order_items_quantity"),
        CheckConstraint("unit_price >= 0", name="ck_order_items_unit_price"),
        CheckConstraint("line_total = quantity * unit_price", name="ck_order_items_line_total"),
        Index("ix_order_items_product", "product_id"),
    )


class CustomerProductProfile(Base):
    """Generator ground truth: one row per (customer, product) the customer was assigned."""
    __tablename__ = "customer_product_profiles"

    customer_id: Mapped[str] = mapped_column(
        String(20), ForeignKey("customers.customer_id", ondelete="RESTRICT"), primary_key=True)
    product_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("products.product_id", ondelete="RESTRICT"), primary_key=True)
    product_type: Mapped[str] = mapped_column(String(50), nullable=False)
    intended_interval_days: Mapped[Decimal] = mapped_column(Numeric(7, 1), nullable=False)
    intended_mean_quantity: Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False)
    active_from: Mapped[dt.date] = mapped_column(Date, nullable=False)
    active_until: Mapped[dt.date] = mapped_column(Date, nullable=False)
    late_adopter: Mapped[bool] = mapped_column(Boolean, nullable=False)
    dropped_out: Mapped[bool] = mapped_column(Boolean, nullable=False)
    purchase_occasions: Mapped[int] = mapped_column(Integer, nullable=False)

    customer: Mapped[Customer] = relationship(back_populates="profiles")
    product: Mapped[Product] = relationship(back_populates="profiles", foreign_keys=[product_id])

    __table_args__ = (
        ForeignKeyConstraint(["product_id", "product_type"], ["products.product_id", "products.product_type"],
                             name="fk_profiles_product_type", ondelete="RESTRICT"),
        CheckConstraint("intended_interval_days > 0", name="ck_profiles_interval"),
        CheckConstraint("intended_mean_quantity > 0", name="ck_profiles_quantity"),
        CheckConstraint("active_from <= active_until", name="ck_profiles_active_period"),
        CheckConstraint("purchase_occasions >= 0", name="ck_profiles_occasions"),
        Index("ix_profiles_product", "product_id"),
    )


# Dependency (load) order; reverse it for deletes.
LOAD_ORDER = [Product, Customer, Order, OrderItem, CustomerProductProfile]
