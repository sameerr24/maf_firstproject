import { useState } from "react";
import ProductImage from "./ProductImage.jsx";
import { STATUS, discountPercent, formatPrice, plural, timingText } from "../format.js";

export default function ProductCard({ rec, onSubscribe }) {
  const [showWhy, setShowWhy] = useState(false);
  const status = STATUS[rec.replenishment_status] ?? { label: rec.replenishment_status, tone: "neutral" };
  const off = discountPercent(rec.price, rec.discount_price);
  const every = rec.expected_purchase_interval == null ? null : Math.max(1, Math.round(rec.expected_purchase_interval));

  return (
    <article className={`card${rec.replenishment_status === "Lapsed" ? " card--dim" : ""}`}
             aria-labelledby={`p-${rec.product_id}`}>
      <div className="card__media">
        <span className="rank" aria-label={`Rank ${rec.rank}`}>#{rec.rank}</span>
        <span className={`badge badge--${status.tone}`} title={rec.replenishment_status}>{status.label}</span>
        <ProductImage src={rec.product_image} alt={rec.product_name} category={rec.category} />
      </div>

      <div className="card__body">
        <p className="card__brand">{rec.brand}</p>
        <h3 className="card__title" id={`p-${rec.product_id}`}>{rec.product_name}</h3>
        <p className="card__pack">{rec.pack_size}</p>

        <div className="price">
          <span className="price__now">{formatPrice(rec.discount_price ?? rec.price)}</span>
          {off > 0 && <span className="price__was">{formatPrice(rec.price)}</span>}
          {off > 0 && <span className="price__off">{off}% off</span>}
        </div>
        <p className="price__note">Subscriber price per pack</p>

        <ul className="facts">
          <li><span className="facts__label">Timing</span><span>{timingText(rec) || "Not enough history"}</span></li>
          <li><span className="facts__label">You buy</span>
            <span>{every ? `every ~${plural(every, "day")}` : `${plural(rec.purchase_count, "time")} so far`}</span></li>
          <li><span className="facts__label">Usually</span>
            <span>{rec.suggested_quantity ? plural(rec.suggested_quantity, "pack") : "-"}</span></li>
        </ul>

        <div className="match" title={`Recommendation score ${rec.final_score}`}>
          <span className="match__bar"><span style={{ width: `${Math.round(rec.final_score * 100)}%` }} /></span>
          <span className="match__value">{Math.round(rec.final_score * 100)}% match</span>
        </div>

        <button type="button" className="why" aria-expanded={showWhy} onClick={() => setShowWhy((v) => !v)}>
          {showWhy ? "Hide reason" : "Why this?"}
        </button>
        {showWhy && <p className="card__why">{rec.explanation}</p>}
      </div>

      <div className="card__footer">
        <button type="button" className="btn btn--primary btn--block" onClick={() => onSubscribe(rec)}>
          Subscribe now
        </button>
      </div>
    </article>
  );
}
