"""Order history (read-only): the customer's orders, newest first, with their items.

Covers both normal orders (source='manual') and orders placed by subscriptions
(source='subscription'); the latter carry the subscription they came from.
Another customer's order raises NotFound.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.errors import NotFound, ValidationError
from app.models import ORDER_SOURCES, DeliveryOccurrence
from database.models import Order, OrderItem

DEFAULT_LIMIT, MAX_LIMIT = 20, 100


def _money(d) -> str:
    return f"{d:.2f}"


def _item_to_dict(item: OrderItem) -> dict[str, Any]:
    p = item.product
    return {
        "order_item_id": item.order_item_id,
        "product": {"product_id": p.product_id, "product_name": p.product_name, "brand": p.brand,
                    "pack_size": p.pack_size, "image_url": p.image_url, "category": p.category},
        "quantity": item.quantity,
        "unit_price": _money(item.unit_price),
        "line_total": _money(item.line_total),
    }


def _order_to_dict(order: Order, subscription_id: int | None) -> dict[str, Any]:
    return {
        "order_id": order.order_id,
        "order_date": order.order_date.isoformat(),
        "order_datetime": order.order_datetime.isoformat(),   # local (Asia/Kolkata) time, no offset
        "source": order.source,
        "subscription_id": subscription_id,
        "delivery_slot": order.delivery_slot,
        "item_count": order.item_count,
        "unit_count": order.unit_count,
        "order_total": _money(order.order_total),
        "items": [_item_to_dict(i) for i in sorted(order.items, key=lambda i: i.order_item_id)],
    }


def _subscription_ids(session: Session, order_ids: list[str]) -> dict[str, int]:
    if not order_ids:
        return {}
    rows = session.execute(select(DeliveryOccurrence.order_id, DeliveryOccurrence.subscription_id)
                           .where(DeliveryOccurrence.order_id.in_(order_ids))).all()
    return dict(rows)


def _parse_int(name: str, raw: Any, default: int, lo: int, hi: int | None = None) -> int:
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise ValidationError(f"{name} must be a whole number", details={"field": name}) from None
    if value < lo or (hi is not None and value > hi):
        bounds = f"between {lo} and {hi}" if hi is not None else f"at least {lo}"
        raise ValidationError(f"{name} must be {bounds}", details={"field": name})
    return value


def list_orders(session: Session, customer_id: str, *, limit: Any = None, offset: Any = None,
                source: str | None = None) -> dict[str, Any]:
    """One page of the customer's orders, newest first. `source` filters manual/subscription."""
    limit = _parse_int("limit", limit, DEFAULT_LIMIT, 1, MAX_LIMIT)
    offset = _parse_int("offset", offset, 0, 0)
    if source is not None and source not in ORDER_SOURCES:
        raise ValidationError(f"source must be one of {', '.join(ORDER_SOURCES)}", details={"field": "source"})

    where = [Order.customer_id == customer_id]
    if source is not None:
        where.append(Order.source == source)
    total = session.scalar(select(func.count()).select_from(Order).where(*where))
    orders = session.scalars(
        select(Order).where(*where)
        .order_by(Order.order_datetime.desc(), Order.order_id.desc())
        .limit(limit).offset(offset)
        .options(selectinload(Order.items).selectinload(OrderItem.product))
    ).all()
    sub_ids = _subscription_ids(session, [o.order_id for o in orders])
    return {
        "orders": [_order_to_dict(o, sub_ids.get(o.order_id)) for o in orders],
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_more": offset + len(orders) < total,
    }


def get_order(session: Session, customer_id: str, order_id: str) -> dict[str, Any]:
    order = session.scalars(
        select(Order).where(Order.order_id == order_id, Order.customer_id == customer_id)
        .options(selectinload(Order.items).selectinload(OrderItem.product))
    ).one_or_none()
    if order is None:
        raise NotFound("Order not found")
    return _order_to_dict(order, _subscription_ids(session, [order.order_id]).get(order.order_id))
