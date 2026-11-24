"""Inspect the BigBasket product catalogue CSV.

Examples (run from the project root):
    python scripts/inspect_dataset.py                      # full overview
    python scripts/inspect_dataset.py --search toothpaste  # keyword search
    python scripts/inspect_dataset.py --search "sona masoori" --search atta --limit 10
    python scripts/inspect_dataset.py --subcategory "Curd"  # list one subcategory
    python scripts/inspect_dataset.py --search butter --no-overview

The dataset is a product CATALOGUE (one row per SKU). It contains no customers,
orders or purchase timestamps; ``Quantity`` is the SKU's pack size.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG = PROJECT_ROOT / "config" / "product_selection.json"
SKU_URL_RE = re.compile(r"/pd/(\d+)(?:/|$)")


def default_csv() -> Path:
    try:
        return PROJECT_ROOT / json.loads(CONFIG.read_text(encoding="utf-8"))["source_csv"]
    except (OSError, KeyError, ValueError):
        return PROJECT_ROOT / "BigBasket.csv"


def section(title: str) -> None:
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")


def overview(df: pd.DataFrame, top: int, samples: int) -> None:
    section("SHAPE")
    print(f"Rows: {df.shape[0]:,}   Columns: {df.shape[1]}")

    section("COLUMNS & DATA TYPES")
    print(pd.DataFrame({"dtype": df.dtypes.astype(str), "non_null": df.notna().sum(),
                        "missing": df.isna().sum(), "unique": df.nunique()}).to_string())

    section("DUPLICATES")
    print(f"Fully duplicated rows:              {df.duplicated().sum():,}")
    print(f"Duplicate Absolute_Url (same SKU):  {df['Absolute_Url'].duplicated().sum():,}")
    print(f"Duplicate ProductName+Brand+Quantity: {df.duplicated(['ProductName', 'Brand', 'Quantity']).sum():,}")
    print(f"Repeated ProductName values (pack/brand variants): {df['ProductName'].duplicated().sum():,}")

    section("UNIQUE COUNTS")
    print(f"Unique ProductName: {df['ProductName'].nunique():,}")
    print(f"Unique Brand:       {df['Brand'].nunique():,}")
    print(f"Categories:         {df['Category'].nunique():,}")
    print(f"SubCategories:      {df['SubCategory'].nunique():,}")
    print(f"Pack sizes (Quantity values): {df['Quantity'].nunique():,}")
    skus = df["Absolute_Url"].str.extract(SKU_URL_RE, expand=False)
    print(f"Rows with numeric SKU in URL: {skus.notna().sum():,} / {len(df):,}")

    section("PRICES (INR)  -  Price = MRP, DiscountPrice = selling price")
    print(df[["Price", "DiscountPrice"]].describe().round(2).to_string())
    print(f"\nRows where DiscountPrice > Price: {(df['DiscountPrice'] > df['Price']).sum():,}")

    section("CATEGORY COUNTS")
    print(df["Category"].value_counts().to_string())

    section(f"SUBCATEGORY COUNTS (top {top}; use --top 0 for all)")
    sub = df.groupby(["Category", "SubCategory"]).size().sort_values(ascending=False)
    print((sub if top == 0 else sub.head(top)).to_string())

    section(f"TOP {min(top, 20) or 20} BRANDS")
    print(df["Brand"].value_counts().head(min(top, 20) or 20).to_string())

    section(f"TOP PACK SIZES (catalogue Quantity, not purchase quantity)")
    print(df["Quantity"].value_counts().head(15).to_string())

    section(f"SAMPLE PRODUCTS (one per category, max {samples})")
    sample = df.groupby("Category", sort=True).head(1).head(samples)
    print_products(sample)


def print_products(rows: pd.DataFrame) -> None:
    if rows.empty:
        print("(no products)")
        return
    out = rows.assign(SKU=rows["Absolute_Url"].str.extract(SKU_URL_RE, expand=False))
    out = out[["SKU", "ProductName", "Brand", "Quantity", "Price", "DiscountPrice", "SubCategory"]]
    with pd.option_context("display.max_colwidth", 60, "display.width", 200):
        print(out.to_string(index=False))


def search(df: pd.DataFrame, keywords: list[str], limit: int) -> None:
    for kw in keywords:
        terms = kw.lower().split()
        hay = (df["ProductName"] + " " + df["Brand"] + " " + df["SubCategory"]).str.lower()
        mask = pd.Series(True, index=df.index)
        for t in terms:
            mask &= hay.str.contains(re.escape(t))
        hits = df[mask]
        section(f"SEARCH: '{kw}'  ->  {len(hits):,} match(es)" + (f", showing {limit}" if len(hits) > limit else ""))
        print_products(hits.head(limit))


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Inspect the BigBasket catalogue CSV")
    p.add_argument("--csv", type=Path, default=default_csv(), help="Path to the catalogue CSV")
    p.add_argument("--search", "-s", action="append", default=[],
                   help="Keyword search over name/brand/subcategory (all words must match). Repeatable.")
    p.add_argument("--subcategory", action="append", default=[], help="List all products in a subcategory. Repeatable.")
    p.add_argument("--limit", type=int, default=25, help="Max rows per search result (default 25)")
    p.add_argument("--top", type=int, default=40, help="Subcategories to show in overview (0 = all)")
    p.add_argument("--samples", type=int, default=11, help="Sample products to show")
    p.add_argument("--no-overview", action="store_true", help="Skip the overview; only run searches")
    args = p.parse_args(argv)

    if not args.csv.exists():
        print(f"ERROR: CSV not found: {args.csv}", file=sys.stderr)
        return 1
    df = pd.read_csv(args.csv)
    print(f"Catalogue: {args.csv}")

    if not args.no_overview:
        overview(df, args.top, args.samples)
    if args.search:
        search(df, args.search, args.limit)
    for sc in args.subcategory:
        rows = df[df["SubCategory"].str.lower() == sc.lower()]
        section(f"SUBCATEGORY: '{sc}'  ->  {len(rows):,} product(s)")
        print_products(rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
