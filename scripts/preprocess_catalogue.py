"""Curate a small, replenishment-focused product catalogue from BigBasket.csv.

Pipeline:
    1. Read the raw catalogue CSV (read-only; checksum verified before/after).
    2. For each target product type in config/product_selection.json, pick ONE SKU
       (manual override > rule-based match > documented fallback).
    3. Normalise the selected rows (names, brands, prices, pack-size metadata).
    4. Validate the result.
    5. Export selected_products.csv / .json and product_selection_report.md.

Run from the project root:
    python scripts/preprocess_catalogue.py

NOTE: The catalogue's ``Quantity`` column is the *pack size* of the SKU
(e.g. "500 ml", "2x100 g"). It is NOT a purchase quantity; this dataset has no
transactions, customers or orders.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd  # type: ignore[import-not-found]

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "product_selection.json"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "processed"

REQUIRED_SOURCE_COLUMNS = [
    "ProductName", "Brand", "Price", "DiscountPrice", "Image_Url",
    "Quantity", "Category", "SubCategory", "Absolute_Url",
]

OUTPUT_COLUMNS = [
    "product_id", "sku_id", "product_type", "product_name", "original_product_name",
    "brand", "original_brand", "price", "discount_price", "pack_size",
    "pack_count", "pack_unit_size", "pack_unit", "category", "subcategory",
    "image_url", "product_url", "replenishable", "consumption_pattern",
    "selection_method", "source_row_number",
]

SKU_URL_RE = re.compile(r"/pd/(\d+)(?:/|$)")
PACK_RE = re.compile(
    r"^\s*(?:(?P<count>\d+)\s*[xX]\s*)?(?P<size>\d+(?:\.\d+)?)\s*(?P<unit>[a-zA-Z]+)\b"
)
UNIT_ALIASES = {
    "g": "g", "gm": "g", "gms": "g", "gram": "g", "grams": "g",
    "kg": "kg", "kgs": "kg",
    "ml": "ml", "l": "L", "ltr": "L", "litre": "L", "liter": "L",
    "pc": "pcs", "pcs": "pcs", "piece": "pcs", "pieces": "pcs", "teabags": "teabags",
}


class CatalogueValidationError(Exception):
    """Raised when the curated catalogue fails a validation rule."""


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_config(path: Path = DEFAULT_CONFIG) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        raise CatalogueValidationError(f"Selection config not found: {path}")
    except json.JSONDecodeError as e:
        raise CatalogueValidationError(f"Selection config {path} is not valid JSON: {e}")


def resolve_source_csv(config: dict, override: str | None = None) -> Path:
    raw = override or config.get("source_csv", "BigBasket.csv")
    path = Path(raw)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    if not path.exists():
        raise CatalogueValidationError(
            f"Source CSV not found at {path}. Set 'source_csv' in the config or pass --input."
        )
    return path


def load_catalogue(csv_path: Path) -> pd.DataFrame:
    # Read everything as text so nothing is silently reinterpreted; prices are
    # converted explicitly later.
    df = pd.read_csv(csv_path, dtype=str, keep_default_na=False)
    missing = [c for c in REQUIRED_SOURCE_COLUMNS if c not in df.columns]
    if missing:
        raise CatalogueValidationError(
            f"Source CSV is missing required columns: {missing}. Found: {list(df.columns)}"
        )
    df["source_row_number"] = range(len(df))  # traceability only, never used as an ID
    df["sku_id"] = df["Absolute_Url"].map(extract_sku_id)
    return df


def extract_sku_id(url: str) -> str | None:
    m = SKU_URL_RE.search(url or "")
    return m.group(1) if m else None


def make_product_id(sku_id: str | None, name: str, brand: str, pack_size: str) -> str:
    """Stable ID: BigBasket SKU from the product URL, else a content hash."""
    if sku_id:
        return f"bb-{sku_id}"
    key = "|".join(normalize_text(x).lower() for x in (brand, name, pack_size))
    return "bbh-" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", str(value or ""))
    value = value.replace("\u2019", "'").replace("\u2018", "'").replace("\u201c", '"').replace("\u201d", '"')
    return re.sub(r"\s+", " ", value).strip()


def clean_product_name(name: str) -> str:
    """Light, non-lossy cleanup. The original name is always kept separately."""
    name = normalize_text(name)
    name = re.sub(r"\s*\(loose\)\s*", " ", name, flags=re.I)
    name = name.split(" | ")[0]  # drop SEO tails like "... | Helps Keeps Heart Healthy"
    name = re.sub(r"\s+-\s*|\s*-\s+", " - ", name)  # spaced dashes only; keeps "2-Minute"
    name = re.sub(r"\s+", " ", name).strip(" ,;-")
    return name


def clean_brand(brand: str, aliases: dict) -> str:
    brand = normalize_text(brand)
    lookup = {normalize_text(k).lower(): v for k, v in aliases.items() if not k.startswith("_")}
    return lookup.get(brand.lower(), brand)


def to_price(value) -> float | None:
    if value is None:
        return None
    text = re.sub(r"[^\d.\-]", "", str(value))  # strip currency symbols / commas
    num = pd.to_numeric(text, errors="coerce") if text else float("nan")
    return None if pd.isna(num) else round(float(num), 2)


def normalize_pack(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "")).lower()


def parse_pack_size(value: str) -> dict:
    """'2x500 g' -> {count: 2, size: 500.0, unit: 'g'}. Unparseable -> Nones."""
    m = PACK_RE.match(str(value or ""))
    if not m:
        return {"pack_count": None, "pack_unit_size": None, "pack_unit": None}
    unit = UNIT_ALIASES.get(m.group("unit").lower())
    if unit is None:
        return {"pack_count": None, "pack_unit_size": None, "pack_unit": None}
    return {
        "pack_count": int(m.group("count")) if m.group("count") else 1,
        "pack_unit_size": float(m.group("size")),
        "pack_unit": unit,
    }


def _rx(patterns) -> list[re.Pattern]:
    return [re.compile(p, re.I) for p in (patterns or [])]


# --------------------------------------------------------------------------- #
# Matching
# --------------------------------------------------------------------------- #
@dataclass
class MatchResult:
    target: dict
    row: pd.Series | None = None
    method: str = "missing"            # auto | manual_override | fallback | missing
    candidate_count: int = 0
    alternatives: list[dict] = field(default_factory=list)
    uncertainties: list[str] = field(default_factory=list)
    fallback_reason: str | None = None


def filter_candidates(df: pd.DataFrame, match: dict, global_excl: dict) -> pd.DataFrame:
    mask = pd.Series(True, index=df.index)
    if match.get("categories"):
        mask &= df["Category"].isin(match["categories"])
    if match.get("subcategories"):
        mask &= df["SubCategory"].isin(match["subcategories"])
    names = df["ProductName"]
    inc = _rx(match.get("name_include_any"))
    if inc:
        mask &= names.map(lambda n: any(p.search(n) for p in inc))
    exc = _rx(match.get("name_exclude_any")) + _rx(global_excl.get("name_patterns"))
    if exc:
        mask &= ~names.map(lambda n: any(p.search(n) for p in exc))
    pack_exc = _rx(global_excl.get("pack_size_patterns"))
    if pack_exc:
        mask &= ~df["Quantity"].map(lambda q: any(p.search(q.strip()) for p in pack_exc))
    return df[mask]


def rank_candidates(cands: pd.DataFrame, match: dict, aliases: dict) -> pd.DataFrame:
    """Deterministic ranking; lower tuple = better. Ties broken by numeric SKU id."""
    brands = [b.lower() for b in match.get("preferred_brands", [])]
    name_prefs = _rx(match.get("preferred_name_patterns"))
    packs = [normalize_pack(p) for p in match.get("preferred_pack_sizes", [])]
    subcats = match.get("subcategories", [])

    def first_idx(items, pred):
        return next((i for i, it in enumerate(items) if pred(it)), len(items))

    ranked = cands.copy()
    ranked["_missing_data"] = (
        ranked["DiscountPrice"].map(to_price).isna().astype(int)
        + ranked["Price"].map(to_price).isna().astype(int)
        + (ranked["Image_Url"].str.strip() == "").astype(int)
    )
    ranked["_brand_rank"] = ranked["Brand"].map(
        lambda b: first_idx(brands, lambda x: x in {normalize_text(b).lower(), clean_brand(b, aliases).lower()})
    )
    ranked["_name_rank"] = ranked["ProductName"].map(
        lambda n: first_idx(name_prefs, lambda p: p.search(normalize_text(n)))
    )
    ranked["_pack_rank"] = ranked["Quantity"].map(lambda q: first_idx(packs, lambda p: p == normalize_pack(q)))
    ranked["_subcat_rank"] = ranked["SubCategory"].map(lambda s: first_idx(subcats, lambda x: x == s))
    ranked["_sku_num"] = pd.to_numeric(ranked["sku_id"], errors="coerce").fillna(float("inf"))
    ranked["_price_num"] = ranked["DiscountPrice"].map(to_price).fillna(float("inf"))
    keys = ["_missing_data", "_brand_rank", "_name_rank", "_pack_rank", "_subcat_rank", "_price_num", "_sku_num"]
    return ranked.sort_values(keys, kind="mergesort")


RANK_KEYS_FOR_TIE = ["_missing_data", "_brand_rank", "_name_rank", "_pack_rank", "_subcat_rank"]


def _alt_summary(row: pd.Series) -> dict:
    return {
        "sku_id": row["sku_id"], "name": row["ProductName"], "brand": row["Brand"],
        "pack_size": row["Quantity"], "discount_price": to_price(row["DiscountPrice"]),
        "subcategory": row["SubCategory"],
    }


def select_for_target(df: pd.DataFrame, target: dict, config: dict) -> MatchResult:
    res = MatchResult(target=target)
    aliases = config.get("brand_aliases", {})
    gexcl = config.get("global_exclusions", {})

    # 1) Manual override wins.
    manual = target.get("manual_sku_id")
    if manual not in (None, ""):
        hit = df[df["sku_id"] == str(manual)]
        if hit.empty:
            raise CatalogueValidationError(
                f"[{target['product_type']}] manual_sku_id '{manual}' does not exist in the source CSV. "
                "Use the numeric id from the product URL (https://www.bigbasket.com/pd/<id>/...)."
            )
        res.row, res.method, res.candidate_count = hit.iloc[0], "manual_override", 1
        res.uncertainties.append(f"Manual override in config selected SKU {manual}.")
        auto = filter_candidates(df, target.get("match", {}), gexcl)
        res.alternatives = [_alt_summary(r) for _, r in rank_candidates(auto, target.get("match", {}), aliases).head(5).iterrows()]
        return res

    # 2) Rule-based match, then 3) documented fallback.
    attempts = [("auto", target.get("match", {}), None)]
    if target.get("fallback"):
        attempts.append(("fallback", target["fallback"].get("match", {}), target["fallback"].get("reason")))

    for method, match, reason in attempts:
        cands = filter_candidates(df, match, gexcl)
        if cands.empty:
            if method == "auto":
                res.uncertainties.append("No SKU matched the primary rules.")
            continue
        ranked = rank_candidates(cands, match, aliases)
        best = ranked.iloc[0]
        res.row, res.method, res.candidate_count = best, method, len(ranked)
        res.fallback_reason = reason
        res.alternatives = [_alt_summary(r) for _, r in ranked.iloc[1:6].iterrows()]

        if method == "fallback":
            res.uncertainties.append(f"Substitute product used: {reason}")
        if len(ranked) > 1 and all(ranked.iloc[1][k] == best[k] for k in RANK_KEYS_FOR_TIE):
            res.uncertainties.append(
                f"Tie with SKU {ranked.iloc[1]['sku_id']} ({ranked.iloc[1]['ProductName']}, "
                f"{ranked.iloc[1]['Quantity']}); chosen by lower price / SKU id."
            )
        if match.get("preferred_brands") and best["_brand_rank"] >= len(match["preferred_brands"]):
            res.uncertainties.append("Selected brand is not in preferred_brands.")
        if match.get("preferred_pack_sizes") and best["_pack_rank"] >= len(match["preferred_pack_sizes"]):
            res.uncertainties.append(f"Pack size '{best['Quantity']}' is not in preferred_pack_sizes.")
        if match.get("preferred_name_patterns") and best["_name_rank"] >= len(match["preferred_name_patterns"]):
            res.uncertainties.append("No candidate matched preferred_name_patterns.")
        price, disc = to_price(best["Price"]), to_price(best["DiscountPrice"])
        if price is not None and disc is not None and disc > price:
            res.uncertainties.append(f"DiscountPrice ({disc}) is higher than Price ({price}).")
        return res

    res.method = "missing"
    return res


# --------------------------------------------------------------------------- #
# Normalisation
# --------------------------------------------------------------------------- #
def build_record(res: MatchResult, config: dict) -> dict:
    row, target = res.row, res.target
    overrides = {k: v for k, v in config.get("display_name_overrides", {}).items() if not k.startswith("_")}
    image_overrides = {k: v for k, v in config.get("image_url_overrides", {}).items() if not k.startswith("_")}
    sku = row["sku_id"]
    name = overrides.get(sku) or clean_product_name(row["ProductName"])
    record = {
        "product_id": make_product_id(sku, row["ProductName"], row["Brand"], row["Quantity"]),
        "sku_id": sku,
        "product_type": target["product_type"],
        "product_name": name,
        "original_product_name": row["ProductName"],
        "brand": clean_brand(row["Brand"], config.get("brand_aliases", {})),
        "original_brand": row["Brand"],
        "price": to_price(row["Price"]),
        "discount_price": to_price(row["DiscountPrice"]),
        "pack_size": row["Quantity"],
        **parse_pack_size(row["Quantity"]),
        "category": row["Category"],
        "subcategory": row["SubCategory"],
        "image_url": image_overrides.get(sku) or row["Image_Url"],   # dataset image may have been replaced
        "product_url": row["Absolute_Url"],
        "replenishable": bool(target.get("replenishable", True)),
        "consumption_pattern": target.get("consumption_pattern"),
        "selection_method": res.method,
        "source_row_number": int(row["source_row_number"]),
    }
    return record


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #
def validate_config(config: dict) -> None:
    targets = config.get("targets")
    if not isinstance(targets, list) or not targets:
        raise CatalogueValidationError("Config must contain a non-empty 'targets' list.")
    for i, t in enumerate(targets):
        if not t.get("product_type"):
            raise CatalogueValidationError(f"Target #{i} is missing 'product_type'.")
    types = [t["product_type"] for t in targets]
    dupes = sorted({t for t in types if types.count(t) > 1})
    if dupes and not config.get("allow_duplicate_product_types", False):
        raise CatalogueValidationError(
            f"Duplicate product_type(s) in config: {dupes}. Set allow_duplicate_product_types=true to permit."
        )


def validate_selection(selected: pd.DataFrame, source: pd.DataFrame, config: dict) -> None:
    errors = []
    max_n = int(config.get("max_products", 20))
    if len(selected) > max_n:
        errors.append(f"Selected {len(selected)} products; the maximum is {max_n}.")
    missing_cols = [c for c in OUTPUT_COLUMNS if c not in selected.columns]
    if missing_cols:
        errors.append(f"Output is missing required columns: {missing_cols}")
    if selected["product_id"].duplicated().any():
        errors.append(f"Duplicate product_id(s): {selected.loc[selected['product_id'].duplicated(), 'product_id'].tolist()}")
    if selected["product_url"].duplicated().any():
        errors.append("The same SKU was selected for more than one product type.")
    if not config.get("allow_duplicate_product_types", False) and selected["product_type"].duplicated().any():
        errors.append(f"Duplicate product_type(s): {selected.loc[selected['product_type'].duplicated(), 'product_type'].tolist()}")
    for col in ("price", "discount_price"):
        bad = selected[selected[col].notna() & ~selected[col].map(lambda v: isinstance(v, (int, float)))]
        if not bad.empty:
            errors.append(f"Column '{col}' has non-numeric values for: {bad['product_id'].tolist()}")
        if selected[col].isna().any():
            errors.append(f"Column '{col}' is missing for: {selected.loc[selected[col].isna(), 'product_id'].tolist()}")
    if selected["replenishable"].map(lambda v: not isinstance(v, bool)).any():
        errors.append("Column 'replenishable' must be Boolean.")

    # Every selected row must exist verbatim in the source CSV (image_url may instead come
    # from image_url_overrides in the config).
    src = source.set_index("Absolute_Url")
    image_overrides = {k: v for k, v in config.get("image_url_overrides", {}).items() if not k.startswith("_")}
    for _, r in selected.iterrows():
        if r["product_url"] not in src.index:
            errors.append(f"{r['product_id']}: product_url not found in the source CSV.")
            continue
        s = src.loc[r["product_url"]]
        checks = {
            "original_product_name": "ProductName", "original_brand": "Brand", "image_url": "Image_Url",
            "pack_size": "Quantity", "category": "Category", "subcategory": "SubCategory",
        }
        for out_col, src_col in checks.items():
            expected = image_overrides.get(r["sku_id"], s[src_col]) if out_col == "image_url" else s[src_col]
            if r[out_col] != expected:
                errors.append(f"{r['product_id']}: '{out_col}' differs from source '{src_col}'.")
        for out_col, src_col in (("price", "Price"), ("discount_price", "DiscountPrice")):
            if r[out_col] != to_price(s[src_col]):
                errors.append(f"{r['product_id']}: '{out_col}' differs from source '{src_col}'.")
        if not str(r["image_url"]).startswith("http") or not str(r["product_url"]).startswith("http"):
            errors.append(f"{r['product_id']}: image_url/product_url is not a URL.")

    if errors:
        raise CatalogueValidationError("Validation failed:\n  - " + "\n  - ".join(errors))


# --------------------------------------------------------------------------- #
# Pipeline
# --------------------------------------------------------------------------- #
def run_pipeline(config_path: Path = DEFAULT_CONFIG, output_dir: Path = DEFAULT_OUTPUT_DIR,
                 input_csv: str | None = None, verbose: bool = True) -> dict:
    config = load_config(config_path)
    validate_config(config)
    csv_path = resolve_source_csv(config, input_csv)
    sha_before = file_sha256(csv_path)

    source = load_catalogue(csv_path)
    results = [select_for_target(source, t, config) for t in config["targets"]]
    matched = [r for r in results if r.row is not None]
    missing = [r for r in results if r.row is None]

    selected = pd.DataFrame([build_record(r, config) for r in matched], columns=OUTPUT_COLUMNS)
    validate_selection(selected, source, config)
    known_skus = set(source["sku_id"].dropna())
    stale = [k for k in config.get("display_name_overrides", {}) if not k.startswith("_") and k not in known_skus]
    if stale:
        print(f"WARNING: display_name_overrides keys not found in the CSV (ignored): {stale}", file=sys.stderr)

    sha_after = file_sha256(csv_path)
    if sha_before != sha_after:
        raise CatalogueValidationError(f"The source CSV {csv_path} was modified during processing!")

    output_dir.mkdir(parents=True, exist_ok=True)
    csv_out = output_dir / "selected_products.csv"
    json_out = output_dir / "selected_products.json"
    report_out = output_dir / "product_selection_report.md"

    selected.to_csv(csv_out, index=False, lineterminator="\n")
    payload = {
        "source_csv": csv_path.name,
        "source_sha256": sha_before,
        "source_row_count": len(source),
        "product_count": len(selected),
        "missing_product_types": [r.target["product_type"] for r in missing],
        "products": json.loads(selected.to_json(orient="records", force_ascii=False)),
    }
    with open(json_out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
        f.write("\n")
    report_out.write_text(render_report(source, csv_path, sha_before, selected, results), encoding="utf-8", newline="\n")

    if verbose:
        print(f"Source: {csv_path} ({len(source):,} rows, sha256 {sha_before[:12]}..., unchanged)")
        print(f"Selected {len(selected)} / {len(results)} target product types\n")
        print(selected[["product_id", "product_type", "product_name", "brand", "pack_size", "discount_price", "selection_method"]]
              .to_string(index=False))
        if missing:
            print("\nMISSING product types:", ", ".join(r.target["product_type"] for r in missing))
        flagged = [r for r in results if r.uncertainties]
        if flagged:
            print("\nItems to review:")
            for r in flagged:
                for u in r.uncertainties:
                    print(f"  - {r.target['product_type']}: {u}")
        print(f"\nWrote:\n  {csv_out}\n  {json_out}\n  {report_out}")

    return {"selected": selected, "results": results, "paths": (csv_out, json_out, report_out),
            "source_sha256": sha_before}


# --------------------------------------------------------------------------- #
# Report
# --------------------------------------------------------------------------- #
def _md(value) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def render_report(source, csv_path, sha, selected, results) -> str:
    matched = [r for r in results if r.row is not None and r.method != "fallback"]
    fallback = [r for r in results if r.method == "fallback"]
    missing = [r for r in results if r.row is None]
    overrides = [r for r in results if r.method == "manual_override"]
    lines = [
        "# Product Selection Report",
        "",
        "_Generated by `scripts/preprocess_catalogue.py` from `config/product_selection.json`. "
        "Do not edit by hand; edit the config and re-run._",
        "",
        "## Summary",
        "",
        f"- Source file: `{csv_path.name}` (sha256 `{sha}`, unchanged by processing)",
        f"- Total original catalogue entries: **{len(source):,}**",
        f"- Target product types: **{len(results)}**",
        f"- Total selected products: **{len(selected)}**",
        f"- Matched directly: **{len(matched)}**" + (f" (of which {len(overrides)} via manual override)" if overrides else ""),
        f"- Matched with a substitute (fallback): **{len(fallback)}**"
        + (f" ({', '.join(r.target['product_type'] for r in fallback)})" if fallback else ""),
        f"- Missing: **{len(missing)}**" + (f" ({', '.join(r.target['product_type'] for r in missing)})" if missing else ""),
        "",
        "> `pack_size` is the catalogue pack size of the SKU (from the `Quantity` column), "
        "not a quantity anyone purchased. `price` = MRP (`Price`), `discount_price` = selling price (`DiscountPrice`), in INR.",
        "",
        "## Selected products",
        "",
        "| # | product_id | product_type | Product name | Brand | Pack size | Price | Discount price | Category / Subcategory | Method |",
        "|---|---|---|---|---|---|---:|---:|---|---|",
    ]
    for i, r in enumerate(selected.itertuples(), 1):
        lines.append(
            f"| {i} | `{r.product_id}` | {r.product_type} | {_md(r.product_name)} | {_md(r.brand)} | {_md(r.pack_size)} "
            f"| {r.price:.2f} | {r.discount_price:.2f} | {_md(r.category)} / {_md(r.subcategory)} | {r.selection_method} |"
        )

    lines += ["", "## Product URLs", ""]
    for r in selected.itertuples():
        lines.append(f"- `{r.product_id}` ({r.product_type}): {r.product_url}")

    lines += ["", "## Target type coverage", "", "| Target type | Status | Selected SKU | Candidates considered |", "|---|---|---|---:|"]
    status_label = {"auto": "matched", "manual_override": "matched (manual override)",
                    "fallback": "substitute", "missing": "**MISSING**"}
    for r in results:
        sku = r.row["sku_id"] if r.row is not None else "-"
        lines.append(f"| {r.target['product_type']} | {status_label[r.method]} | {sku} | {r.candidate_count} |")

    lines += ["", "## Missing target types", ""]
    lines += [f"- **{r.target['product_type']}**: no SKU matched the rules"
              + (" and no fallback is configured." if not r.target.get("fallback") else " or the fallback rules.")
              for r in missing] or ["None. Every target type has a selected SKU (see substitutes below)."]

    lines += ["", "## Substitutes (fallbacks) used", ""]
    lines += [f"- **{r.target['product_type']}** → `{r.row['ProductName']}` ({r.row['Brand']}, {r.row['Quantity']}). {r.fallback_reason}"
              for r in fallback] or ["None."]

    lines += ["", "## Manual overrides used", ""]
    lines += [f"- **{r.target['product_type']}** → SKU `{r.row['sku_id']}` ({r.row['ProductName']}, {r.row['Quantity']})"
              for r in overrides] or ["None. All selections came from the matching rules in the config."]

    lines += ["", "## Matching uncertainties", ""]
    unc = [(r.target["product_type"], u) for r in results for u in r.uncertainties]
    lines += [f"- **{t}**: {u}" for t, u in unc] or ["None detected."]

    lines += ["", "## Alternatives per product type", "",
              "Use these to swap a selection: copy the SKU id into `manual_sku_id` for that target in "
              "`config/product_selection.json`, then re-run `python scripts/preprocess_catalogue.py`.", ""]
    for r in results:
        lines.append(f"### {r.target['product_type']}")
        if r.row is not None:
            lines.append(f"Selected: SKU `{r.row['sku_id']}`, {_md(r.row['ProductName'])} ({_md(r.row['Brand'])}, {_md(r.row['Quantity'])})")
        lines.append("")
        if r.alternatives:
            lines += ["| SKU | Name | Brand | Pack | Discount price | Subcategory |", "|---|---|---|---|---:|---|"]
            for a in r.alternatives:
                lines.append(f"| {a['sku_id']} | {_md(a['name'])} | {_md(a['brand'])} | {_md(a['pack_size'])} "
                             f"| {a['discount_price']} | {_md(a['subcategory'])} |")
        else:
            lines.append("_No other candidates matched the rules._")
        lines.append("")

    lines += [
        "## How to change a selection",
        "",
        "1. Find the SKU you want, e.g. `python scripts/inspect_dataset.py --search \"brown bread\"` (the SKU id is the number in the product URL).",
        "2. In `config/product_selection.json`, set `manual_sku_id` for that target to the SKU id (as a string), "
        "or adjust `preferred_brands` / `preferred_pack_sizes` / name patterns.",
        "3. Re-run `python scripts/preprocess_catalogue.py` and `pytest`.",
        "",
    ]
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Curate the replenishment product catalogue from BigBasket.csv")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="Selection config JSON")
    parser.add_argument("--input", help="Override the source CSV path from the config")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR, help="Where to write outputs")
    args = parser.parse_args(argv)
    try:
        run_pipeline(args.config, args.output_dir, args.input)
    except CatalogueValidationError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
