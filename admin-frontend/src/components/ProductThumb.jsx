import { useState } from "react";

// The customer site's local copy (/products/<sku>.jpg, served from frontend/public), then the
// catalogue's image_url, then the product's initial.
function localImage(src) {
  const sku = /\/(\d+)_[^/]*$/.exec(src ?? "")?.[1];
  return sku ? `/products/${sku}.jpg` : null;
}

export default function ProductThumb({ product, size = 44 }) {
  const sources = [localImage(product.image_url), product.image_url].filter(Boolean);
  const [failed, setFailed] = useState({ src: product.image_url, count: 0 });
  const attempt = failed.src === product.image_url ? failed.count : 0;
  const style = { width: size, height: size };
  if (attempt >= sources.length) {
    return <span className="thumb thumb--empty" style={style} aria-hidden="true">{product.product_name?.[0] ?? "?"}</span>;
  }
  return (
    <img className="thumb" style={style} src={sources[attempt]} alt="" loading="lazy" referrerPolicy="no-referrer"
         onError={() => setFailed({ src: product.image_url, count: attempt + 1 })} />
  );
}
