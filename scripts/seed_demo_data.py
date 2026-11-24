"""Fill the subscription module's app-managed columns with demo data.

    python scripts/seed_demo_data.py --dry-run            # show what would change
    python scripts/seed_demo_data.py                      # fill empty values only
    python scripts/seed_demo_data.py --reset-inventory    # also set demo stock levels

What it writes (one transaction; re-running never overwrites existing values):
  customers.customer_name   deterministic Indian names
  customers.phone_e164      fictional numbers from Ofcom's drama range +44 7700 900000-900999,
                            which are never assigned to real phones, so nobody real can be
                            messaged. Every 10th customer gets no phone (tests the "no WhatsApp" path).
  customers.whatsapp_opt_in left false. Opt in only your own verified test numbers.
  orders.delivery_slot      the slot containing order_datetime (historical orders only)
  inventory.available_qty   only with --reset-inventory: one product out of stock,
                            one nearly out, a few low, the rest well stocked
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # run from anywhere

from sqlalchemy import select, update  # noqa: E402

from app.constants import DELIVERY_SLOTS  # noqa: E402
from app.models import Inventory  # noqa: E402
from database.connection import DatabaseConfigError, make_engine, safe_url, session_scope  # noqa: E402
from database.models import Customer, Order, Product  # noqa: E402

SEED = 20261124
FICTIONAL_PHONE_PREFIX = "+447700900"  # +44 7700 900000-900999 (Ofcom drama numbers)
NO_PHONE_EVERY = 10

FIRST_NAMES = [
    "Aarav", "Aditi", "Akash", "Ananya", "Arjun", "Diya", "Ishaan", "Kavya", "Kabir", "Meera",
    "Neha", "Nikhil", "Pooja", "Priya", "Rahul", "Riya", "Rohan", "Saanvi", "Sahil", "Sneha",
    "Tanvi", "Varun", "Vihaan", "Zara", "Aditya", "Isha", "Karan", "Nisha", "Manav", "Tara",
]
LAST_NAMES = [
    "Sharma", "Verma", "Gupta", "Iyer", "Nair", "Reddy", "Patel", "Shah", "Mehta", "Kapoor",
    "Singh", "Das", "Rao", "Menon", "Joshi", "Kulkarni", "Banerjee", "Chopra", "Malhotra", "Pillai",
]

# product_type -> available_qty. Everything not listed gets DEFAULT_STOCK.
DEMO_STOCK = {
    "bread": 0,      # out of stock: reminder says so, approval is refused
    "eggs": 2,       # nearly out: a 3-unit approval fails, 1-2 units succeed
    "milk": 8,       # low: runs out after a few approvals
    "curd": 5,
}
DEFAULT_STOCK = 100


def slot_for_hour(hour: int) -> str:
    for slot in DELIVERY_SLOTS:
        start, end = (int(part[:2]) for part in slot.split("-"))
        if start <= hour < end:
            return slot
    return DELIVERY_SLOTS[0] if hour < 12 else DELIVERY_SLOTS[-1]


def plan_customers(customers: list[Customer]) -> dict[str, dict]:
    rng = random.Random(SEED)
    used_names: set[str] = set()
    changes = {}
    for i, c in enumerate(sorted(customers, key=lambda c: c.customer_id)):
        if i >= 1000:
            raise RuntimeError("More than 1000 customers: the fictional phone range is exhausted.")
        while True:
            name = f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}"
            if name not in used_names:
                used_names.add(name)
                break
        phone = None if i % NO_PHONE_EVERY == NO_PHONE_EVERY - 1 else f"{FICTIONAL_PHONE_PREFIX}{i:03d}"
        new = {}
        if c.customer_name is None:
            new["customer_name"] = name
        if c.phone_e164 is None and phone is not None:
            new["phone_e164"] = phone
        if new:
            changes[c.customer_id] = new
    return changes


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Seed demo data into the app-managed columns.")
    ap.add_argument("--dry-run", action="store_true", help="Show what would change; write nothing")
    ap.add_argument("--reset-inventory", action="store_true",
                    help="Also set demo stock levels (overwrites current available_qty)")
    args = ap.parse_args(argv)

    try:
        engine = make_engine()
        print(f"Target: {safe_url(engine.url)}")
        with session_scope(engine) as s:
            # Customers ------------------------------------------------------------
            customers = s.scalars(select(Customer)).all()
            cust_changes = plan_customers(customers)
            taken = set(s.scalars(select(Customer.phone_e164).where(Customer.phone_e164.is_not(None))))
            clash = {v["phone_e164"] for v in cust_changes.values() if "phone_e164" in v} & taken
            if clash:
                raise RuntimeError(f"Phone numbers already used by other customers: {sorted(clash)[:5]}")
            n_names = sum("customer_name" in v for v in cust_changes.values())
            n_phones = sum("phone_e164" in v for v in cust_changes.values())
            print(f"customers: {n_names} names, {n_phones} phones to fill "
                  f"({len(customers)} customers; every {NO_PHONE_EVERY}th has no phone; opt-in untouched)")
            for cid, v in list(cust_changes.items())[:3]:
                print(f"  e.g. {cid}: {v}")

            # Orders ---------------------------------------------------------------
            orders = s.execute(select(Order.order_id, Order.order_datetime)
                               .where(Order.delivery_slot.is_(None), Order.source == "manual")).all()
            by_slot: dict[str, list[str]] = {}
            for oid, odt in orders:
                by_slot.setdefault(slot_for_hour(odt.hour), []).append(oid)
            print(f"orders: {len(orders)} delivery slots to fill "
                  f"({', '.join(f'{k}: {len(v)}' for k, v in sorted(by_slot.items()))})")

            # Inventory ------------------------------------------------------------
            stock = {}
            if args.reset_inventory:
                rows = s.execute(select(Inventory.product_id, Product.product_type, Inventory.available_qty)
                                 .join(Product, Product.product_id == Inventory.product_id)).all()
                stock = {pid: DEMO_STOCK.get(ptype, DEFAULT_STOCK) for pid, ptype, _ in rows}
                unknown = set(DEMO_STOCK) - {ptype for _, ptype, _ in rows}
                if unknown:
                    raise RuntimeError(f"DEMO_STOCK names unknown product types: {sorted(unknown)}")
                print("inventory: " + ", ".join(f"{ptype}={DEMO_STOCK[ptype]}" for ptype in DEMO_STOCK)
                      + f", others={DEFAULT_STOCK}")

            if args.dry_run:
                s.rollback()
                print("Dry run: nothing written.")
                return 0

            for cid, values in cust_changes.items():
                s.execute(update(Customer).where(Customer.customer_id == cid).values(**values))
            for slot, ids in by_slot.items():
                s.execute(update(Order).where(Order.order_id.in_(ids)).values(delivery_slot=slot))
            for pid, qty in stock.items():
                s.execute(update(Inventory).where(Inventory.product_id == pid).values(available_qty=qty))
        print("Committed.")
    except (DatabaseConfigError, RuntimeError) as e:
        print(f"ERROR: {e}\nNothing was written.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
