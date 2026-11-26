import { useCallback, useEffect, useState } from "react";
import ProductCard from "../components/ProductCard.jsx";
import { EmptyState, SkeletonGrid } from "../components/States.jsx";
import { fetchMyRecommendations } from "../api/recommendations.js";
import { formatDate, plural } from "../format.js";

const LIMIT = 10;

export default function RecommendationsPage({ session, onSubscribe, onSessionExpired }) {
  const { token, customerName } = session;
  const displayName = customerName?.trim() || "you";
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  const load = useCallback(() => {
    setError(null);
    setData(null);
    fetchMyRecommendations(token, LIMIT)
      .then(setData)
      .catch((e) => (e.status === 401 ? onSessionExpired() : setError(e.message)));  // expired -> sign in again
  }, [token, onSessionExpired]);

  useEffect(load, [load]);

  const recs = data?.recommendations ?? [];

  return (
    <main className="main main--recs">
      <section className="hero hero--compact">
        <div>
          <p className="eyebrow">Recommended for {displayName}</p>
          <h1>Your top {data && recs.length > 0 ? recs.length : LIMIT} reorder picks</h1>
          <p className="lede">Ranked from your purchase history: what you'll need first comes first.</p>
        </div>
        {data && recs.length > 0 && (
          <p className="hero__meta">
            Based on purchases up to <strong>{formatDate(data.reference_date)}</strong>
            <br />{recs.length} of {plural(data.total_eligible_products, "product")} you buy
          </p>
        )}
      </section>

      {error && (
        <div className="alert" role="alert">
          <span>{error}</span>
          <button type="button" className="btn btn--ghost btn--sm" onClick={load}>Try again</button>
        </div>
      )}

      <section className="scroll-panel" aria-label="Recommended products" tabIndex={0}>
        {!data && !error && <SkeletonGrid count={LIMIT} />}

        {data && recs.length === 0 && (
          <EmptyState title="No recommendations yet">
            We recommend products from your own purchase history, and there isn't any yet for {displayName}.
            After a few orders, the essentials you buy regularly will appear here.
          </EmptyState>
        )}

        {recs.length > 0 && (
          <div className="grid">
            {recs.map((rec) => <ProductCard key={rec.product_id} rec={rec} onSubscribe={onSubscribe} />)}
          </div>
        )}
      </section>
    </main>
  );
}
