import ProductImage from "../components/ProductImage.jsx";
import { formatPrice, plural } from "../format.js";

// PLACEHOLDER: the subscription flow is not built yet (it belongs to the subscriptions
// module, docs/SUBSCRIPTIONS_CONTEXT.md). This page already receives the chosen product
// and the engine's recommended quantity and frequency, which the real form should use
// as its pre-filled values.
export default function SubscriptionPage({ product, customerId, onBack }) {
  if (!product) {
    return (
      <main className="main">
        <div className="sub-page">
          <h1>Subscription</h1>
          <p className="muted">This product isn't selected any more. Pick it again from your recommendations.</p>
          <button type="button" className="btn btn--primary" onClick={onBack}>Back to recommendations</button>
        </div>
      </main>
    );
  }

  const every = product.expected_purchase_interval == null
    ? null : Math.max(1, Math.round(product.expected_purchase_interval));
  const price = product.discount_price ?? product.price;

  return (
    <main className="main">
      <button type="button" className="back-link" onClick={onBack}>← Back to recommendations</button>

      <div className="sub-page">
        <div className="sub-page__product">
          <ProductImage src={product.product_image} alt={product.product_name} category={product.category} />
          <div>
            <p className="card__brand">{product.brand}</p>
            <h1>{product.product_name}</h1>
            <p className="card__pack">{product.pack_size} · {formatPrice(price)} per pack (subscriber price)</p>
          </div>
        </div>

        <dl className="summary">
          <div><dt>Customer</dt><dd>{customerId}</dd></div>
          <div><dt>Recommended quantity</dt>
            <dd>{product.suggested_quantity ? plural(product.suggested_quantity, "pack") : "-"}</dd></div>
          <div><dt>Recommended frequency</dt><dd>{every ? `every ${plural(every, "day")}` : "-"}</dd></div>
          {product.suggested_quantity && price != null && (
            <div><dt>Per delivery</dt><dd>{formatPrice(price * product.suggested_quantity)}</dd></div>
          )}
        </dl>

        <div className="coming-soon" role="status">
          <strong>Subscription setup is coming soon.</strong>
          <span>
            This is where you'll confirm the quantity, how often to deliver and your first delivery date,
            then approve each delivery on WhatsApp.
          </span>
        </div>

        <div className="sub-page__actions">
          <button type="button" className="btn btn--ghost" onClick={onBack}>Back to recommendations</button>
          <button type="button" className="btn btn--primary" disabled title="Not available yet">
            Confirm subscription
          </button>
        </div>
      </div>
    </main>
  );
}
