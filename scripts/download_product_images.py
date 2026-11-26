"""Download the catalogue's product images into the frontend, so the demo doesn't depend on
BigBasket's CDN (it can be blocked by ad blockers or networks, and has replaced images before).

Reads data/processed/selected_products.json and saves each image_url as
frontend/public/products/<sku_id>.jpg (converted to JPEG). ProductImage.jsx loads that local
file first and falls back to image_url, then to the category tile.

    .venv/bin/python -m scripts.download_product_images          # skips files already there
    .venv/bin/python -m scripts.download_product_images --force  # download everything again
"""
import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PRODUCTS = ROOT / "data" / "processed" / "selected_products.json"
OUT = ROOT / "frontend" / "public" / "products"
MAX_SIDE = 600          # px; the largest the site shows an image is ~300 px wide


def download_jpeg(url: str, dest: Path) -> None:
    """Fetch with curl (python.org builds on macOS often lack CA certificates), then resize and
    convert to JPEG with macOS `sips`, so no extra dependency is needed."""
    with tempfile.NamedTemporaryFile(suffix=".img") as tmp:
        ctype = subprocess.run(["curl", "-sSfL", "--max-time", "30", "-A", "Mozilla/5.0",   # -L: bigbasket -> bbassets
                                "-o", tmp.name, "-w", "%{content_type}", url],
                               capture_output=True, text=True, check=True).stdout
        if not ctype.startswith("image/"):
            raise ValueError(f"not an image: {ctype}")
        subprocess.run(["sips", "-s", "format", "jpeg", "-s", "formatOptions", "85", "-Z", str(MAX_SIDE),
                        tmp.name, "--out", str(dest)], check=True, capture_output=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--force", action="store_true", help="download images that are already saved")
    args = parser.parse_args()

    products = json.loads(PRODUCTS.read_text())["products"]
    OUT.mkdir(parents=True, exist_ok=True)
    failed = 0
    for p in products:
        dest = OUT / f"{p['sku_id']}.jpg"
        if dest.exists() and not args.force:
            print(f"have  {dest.name}")
            continue
        try:
            download_jpeg(p["image_url"], dest)
            print(f"saved {dest.name}  {dest.stat().st_size // 1024} KB  {p['product_name']}")
        except Exception as e:                                   # report every failure, keep going
            failed += 1
            print(f"FAIL  {p['sku_id']}  {p['image_url']}: {e}", file=sys.stderr)
    print(f"{len(products) - failed}/{len(products)} images in {OUT.relative_to(ROOT)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
