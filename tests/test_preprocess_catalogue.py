import json
import copy

import pandas as pd
import pytest

from scripts import preprocess_catalogue as pc


# ---------------------------------------------------------------- pipeline output
def test_product_ids_are_unique(pipeline_run):
    ids = pipeline_run["selected"]["product_id"]
    assert ids.is_unique, f"Duplicate product ids: {ids[ids.duplicated()].tolist()}"


def test_selected_count_is_at_most_20(pipeline_run, config):
    assert 0 < len(pipeline_run["selected"]) <= 20
    assert config["max_products"] <= 20


def test_output_files_exist(pipeline_run):
    for path in pipeline_run["paths"]:
        assert path.exists() and path.stat().st_size > 0, f"{path} missing or empty"


def test_required_columns_exist(pipeline_run):
    csv_out, json_out, _ = pipeline_run["paths"]
    df = pd.read_csv(csv_out)
    assert list(df.columns) == pc.OUTPUT_COLUMNS
    payload = json.loads(json_out.read_text(encoding="utf-8"))
    assert payload["product_count"] == len(df)
    assert set(pc.OUTPUT_COLUMNS) <= set(payload["products"][0])


def test_price_columns_are_numeric(pipeline_run):
    df = pd.read_csv(pipeline_run["paths"][0])
    for col in ("price", "discount_price"):
        assert pd.api.types.is_numeric_dtype(df[col]), f"{col} is not numeric"
        assert df[col].notna().all() and (df[col] > 0).all()
    assert (df["discount_price"] <= df["price"]).all()


def test_replenishable_is_boolean(pipeline_run):
    payload = json.loads(pipeline_run["paths"][1].read_text(encoding="utf-8"))
    assert all(isinstance(p["replenishable"], bool) for p in payload["products"])


def test_product_types_unique_and_from_config(pipeline_run, config):
    types = pipeline_run["selected"]["product_type"]
    assert types.is_unique
    assert set(types) <= {t["product_type"] for t in config["targets"]}


def test_selected_rows_come_from_source_csv(pipeline_run, source_csv, config):
    src = pd.read_csv(source_csv, dtype=str, keep_default_na=False).set_index("Absolute_Url")
    images = config.get("image_url_overrides", {})
    for r in pipeline_run["selected"].itertuples():
        row = src.loc[r.product_url]
        assert row["ProductName"] == r.original_product_name
        assert images.get(r.sku_id, row["Image_Url"]) == r.image_url     # replaced images come from config
        assert row["Quantity"] == r.pack_size
        assert float(row["Price"]) == r.price
        assert float(row["DiscountPrice"]) == r.discount_price


def test_selection_is_deterministic(pipeline_run, tmp_path):
    second = pc.run_pipeline(pc.DEFAULT_CONFIG, tmp_path, verbose=False)
    for first_path, second_path in zip(pipeline_run["paths"], second["paths"]):
        assert first_path.read_bytes() == second_path.read_bytes(), f"{first_path.name} differs between runs"


def test_original_dataset_unchanged(pipeline_run, source_csv):
    assert pc.file_sha256(source_csv) == pipeline_run["sha_before"]
    assert pipeline_run["source_sha256"] == pipeline_run["sha_before"]


# ---------------------------------------------------------------- unit tests
def test_product_id_from_url_is_stable():
    url = "https://www.bigbasket.com/pd/40075537/fresho-onion-2-kg/"
    assert pc.extract_sku_id(url) == "40075537"
    assert pc.make_product_id("40075537", "Onion", "Fresho", "2 kg") == "bb-40075537"


def test_product_id_hash_fallback_is_deterministic():
    a = pc.make_product_id(None, "Onion  (Loose)", "Fresho", "2 kg")
    b = pc.make_product_id(None, "Onion (Loose)", "fresho", "2 kg")
    assert a == b and a.startswith("bbh-")
    assert a != pc.make_product_id(None, "Onion (Loose)", "Fresho", "1 kg")


@pytest.mark.parametrize("raw,expected", [
    ("500 ml", (1, 500.0, "ml")),
    ("2x500 g", (2, 500.0, "g")),
    ("1 L", (1, 1.0, "L")),
    ("12 pcs", (1, 12.0, "pcs")),
    ("Combo", (None, None, None)),
])
def test_parse_pack_size(raw, expected):
    p = pc.parse_pack_size(raw)
    assert (p["pack_count"], p["pack_unit_size"], p["pack_unit"]) == expected


def test_clean_product_name_keeps_hyphenated_words():
    assert pc.clean_product_name("Onion (Loose)") == "Onion"
    assert pc.clean_product_name("2-Minute Noodles  -Masala") == "2-Minute Noodles - Masala"


@pytest.mark.parametrize("raw,expected", [("₹1,234.50", 1234.5), ("52.00", 52.0), ("", None), ("n/a", None)])
def test_to_price(raw, expected):
    assert pc.to_price(raw) == expected


def test_manual_override_is_respected(source_csv, config):
    df = pc.load_catalogue(source_csv)
    target = copy.deepcopy(next(t for t in config["targets"] if t["product_type"] == "milk"))
    target["manual_sku_id"] = "40075537"  # deliberately an onion SKU
    res = pc.select_for_target(df, target, config)
    assert res.method == "manual_override" and res.row["sku_id"] == "40075537"


def test_unknown_manual_override_raises(source_csv, config):
    df = pc.load_catalogue(source_csv)
    target = copy.deepcopy(config["targets"][0])
    target["manual_sku_id"] = "999999999"
    with pytest.raises(pc.CatalogueValidationError, match="does not exist"):
        pc.select_for_target(df, target, config)


def test_missing_type_is_reported(source_csv, config):
    df = pc.load_catalogue(source_csv)
    target = {"product_type": "unicorn_milk", "match": {"name_include_any": ["unicorn"]}}
    res = pc.select_for_target(df, target, config)
    assert res.method == "missing" and res.row is None


def test_validation_rejects_too_many_products(pipeline_run, source_csv, config):
    df = pc.load_catalogue(source_csv)
    sel = pipeline_run["selected"]
    with pytest.raises(pc.CatalogueValidationError, match="maximum"):
        pc.validate_selection(sel, df, {**config, "max_products": len(sel) - 1})


def test_validation_rejects_duplicate_ids(pipeline_run, source_csv, config):
    df = pc.load_catalogue(source_csv)
    sel = pd.concat([pipeline_run["selected"], pipeline_run["selected"].head(1)], ignore_index=True)
    with pytest.raises(pc.CatalogueValidationError, match="Duplicate product_id"):
        pc.validate_selection(sel, df, config)
