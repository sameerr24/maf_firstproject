import { useState } from "react";

// Catalogue images come from the dataset's image_url (BigBasket's CDN). A copy of each is kept
// in public/products/<sku>.jpg (scripts/download_product_images.py), so the site doesn't depend
// on the CDN, which ad blockers and some networks block. We try the local copy, then the CDN,
// and if neither loads we show an illustrated tile chosen by the product's catalogue category.

const ICONS = {
  dairy: <path d="M9 2.5h6v3l2 3V20a1.5 1.5 0 0 1-1.5 1.5h-7A1.5 1.5 0 0 1 7 20V8.5l2-3zM7 12.5h10" />,
  eggs: <path d="M12 3c3.6 0 6.2 5.4 6.2 10.2a6.2 6.2 0 0 1-12.4 0C5.8 8.4 8.4 3 12 3z" />,
  produce: <path d="M5 19.5C5 11 11 5 19.5 4.5 19 13 13 19 5 19.5zM5 19.5l7.5-7.5" />,
  staples: <path d="M12 21.5V9M12 9c-2.5-1-3.6-3.2-3.6-6 2.4 0 3.6 2.8 3.6 6zm0 0c2.5-1 3.6-3.2 3.6-6-2.4 0-3.6 2.8-3.6 6zm0 5c-2.5-1-3.6-3.2-3.6-6 2.4 0 3.6 2.8 3.6 6zm0 0c2.5-1 3.6-3.2 3.6-6-2.4 0-3.6 2.8-3.6 6z" />,
  care: <path d="M12 3c3.2 4.2 6 7.3 6 11a6 6 0 0 1-12 0c0-3.7 2.8-6.8 6-11zM9.5 14.5a2.5 2.5 0 0 0 2.5 2.5" />,
  cleaning: <path d="M12 3v4M12 17v4M3 12h4M17 12h4M5.6 5.6l2.8 2.8M15.6 15.6l2.8 2.8M5.6 18.4l2.8-2.8M15.6 8.4l2.8-2.8" />,
  snacks: <path d="M12 3a9 9 0 1 0 9 9 3 3 0 0 1-3-3 3 3 0 0 1-3-3 3 3 0 0 1-3-3zM8.5 10.5h.01M12 15h.01M15.5 13.5h.01M8.5 15.5h.01" />,
  beverages: <path d="M4 9h13v4.5A5.5 5.5 0 0 1 11.5 19h-2A5.5 5.5 0 0 1 4 13.5zM17 10h1.5a2.5 2.5 0 0 1 0 5H17M8 3.5v2.5M12 3.5v2.5" />,
  other: <path d="M5 8h14l-1.2 12.5H6.2zM9 8a3 3 0 0 1 6 0" />,
};

// Keyword match on the catalogue category text (presentation only, not product names).
const THEMES = [
  ["dairy", /dairy|bakery/i, "#e7f1fb", "#2b5d8a"],
  ["eggs", /egg/i, "#fdf1df", "#94601a"],
  ["produce", /fruit|vegetable/i, "#e5f4e6", "#2f6b34"],
  ["staples", /foodgrain|oil|masala/i, "#f8efdc", "#8a5a14"],
  ["care", /beauty|hygiene/i, "#f6e9f5", "#7d3a78"],
  ["cleaning", /clean|household/i, "#e3f3f5", "#1d6670"],
  ["snacks", /snack/i, "#fcebe3", "#9a4a24"],
  ["beverages", /beverage/i, "#f1ebe4", "#6b4a2e"],
];

function themeFor(category) {
  const hit = THEMES.find(([, re]) => re.test(category ?? ""));
  return hit ? { icon: hit[0], bg: hit[2], fg: hit[3] } : { icon: "other", bg: "#eef0f2", fg: "#4b5560" };
}

/** The local copy of a BigBasket image: ".../p/l/242671_1-nandini....jpg" -> "/products/242671.jpg".
 *  Keyed by SKU, not file name, so it still works with an older image_url for the same product. */
export function localImage(src) {
  const sku = /\/(\d+)_[^/]*$/.exec(src ?? "")?.[1];
  return sku ? `/products/${sku}.jpg` : null;
}

export default function ProductImage({ src, alt, category }) {
  const sources = [localImage(src), src].filter(Boolean);
  const [failed, setFailed] = useState({ src, count: 0 });     // how many sources failed, for this src
  const attempt = failed.src === src ? failed.count : 0;
  if (attempt >= sources.length) {
    const t = themeFor(category);
    return (
      <div className="product-image product-image--fallback" role="img" aria-label={alt}
           style={{ "--tile-bg": t.bg, "--tile-fg": t.fg }}>
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round"
             strokeLinejoin="round" aria-hidden="true">{ICONS[t.icon]}</svg>
        {category && <span className="product-image__cat">{category}</span>}
      </div>
    );
  }
  return (
    <div className="product-image">
      <img src={sources[attempt]} alt={alt} loading="lazy" referrerPolicy="no-referrer"
           onError={() => setFailed({ src, count: attempt + 1 })} />
    </div>
  );
}
