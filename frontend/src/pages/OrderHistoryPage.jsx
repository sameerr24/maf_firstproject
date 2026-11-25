import { useCallback, useEffect, useState } from "react";
import ProductImage from "../components/ProductImage.jsx";
import { EmptyState } from "../components/States.jsx";
import { getOrders } from "../api/orders.js";
import { formatDate, formatPrice, plural } from "../format.js";
import "../styles/orders.css";

// Order history (subscriptions module, read-only): the customer's orders newest first, both
// regular orders and the ones placed by approving a subscription delivery in chat.

const PAGE_SIZE = 20;
const ITEMS_PREVIEW = 3;
const FILTERS = [
  { key: "all", label: "All orders", source: undefined },
  { key: "subscription", label: "Subscription", source: "subscription" },
  { key: "manual", label: "Regular", source: "manual" },
];

/** "11:16 AM" from the order's local (IST) timestamp, e.g. "2026-11-25T11:16:23". */
function formatTime(isoLocal) {
  const [h, m] = isoLocal.slice(11, 16).split(":").map(Number);
  return `${h % 12 || 12}:${String(m).padStart(2, "0")} ${h < 12 ? "AM" : "PM"}`;
}

export default function OrderHistoryPage({ session, onSessionExpired, onBack }) {
  const token = session?.token;
  const [filter, setFilter] = useState("all");
  const [orders, setOrders] = useState(null);
  const [total, setTotal] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState(null);

  const source = FILTERS.find((f) => f.key === filter).source;

  const fail = useCallback((e) => {
    if (e.status === 401) {
      onSessionExpired();
      return;
    }
    setError(e.message);
  }, [onSessionExpired]);

  const load = useCallback(() => {
    setError(null);
    setOrders(null);
    getOrders(token, { limit: PAGE_SIZE, offset: 0, source })
      .then((page) => {
        setOrders(page.orders);
        setTotal(page.total);
        setHasMore(page.has_more);
      })
      .catch(fail);
  }, [token, source, fail]);

  useEffect(load, [load]);

  const loadMore = () => {
    setLoadingMore(true);
    getOrders(token, { limit: PAGE_SIZE, offset: orders.length, source })
      .then((page) => {
        setOrders((prev) => [...prev, ...page.orders]);
        setTotal(page.total);
        setHasMore(page.has_more);
      })
      .catch(fail)
      .finally(() => setLoadingMore(false));
  };

  return (
    <main className="main">
      <button type="button" className="back-link" onClick={onBack}>← Back to recommendations</button>

      <div className="orders">
        <section className="orders__head">
          <div>
            <h1>Order history</h1>
            <p className="muted">
              {orders ? `${plural(total, "order")}${source ? ` · ${FILTERS.find((f) => f.key === filter).label.toLowerCase()}` : ""}`
                : "Loading…"}
            </p>
          </div>
          <div className="orders__filters" role="tablist" aria-label="Filter orders">
            {FILTERS.map((f) => (
              <button key={f.key} type="button" role="tab" aria-selected={filter === f.key}
                      className={`chip${filter === f.key ? " chip--active" : ""}`} onClick={() => setFilter(f.key)}>
                {f.label}
              </button>
            ))}
          </div>
        </section>

        {error && (
          <div className="alert" role="alert">
            <span>{error}</span>
            <button type="button" className="btn btn--ghost btn--sm" onClick={load}>Try again</button>
          </div>
        )}

        {!orders && !error && (
          <div className="orders__list" aria-busy="true">
            {Array.from({ length: 3 }, (_, i) => <div key={i} className="order order--skeleton skeleton" />)}
          </div>
        )}

        {orders && orders.length === 0 && (
          <EmptyState title={source === "subscription" ? "No subscription orders yet" : "No orders yet"}>
            {source === "subscription"
              ? "When you approve a subscription delivery in chat, the order appears here."
              : "Your orders will appear here."}
          </EmptyState>
        )}

        {orders && orders.length > 0 && (
          <div className="orders__list">
            {orders.map((o) => <OrderCard key={o.order_id} order={o} />)}
          </div>
        )}

        {hasMore && (
          <div className="orders__more">
            <button type="button" className="btn btn--ghost" disabled={loadingMore} onClick={loadMore}>
              {loadingMore ? "Loading…" : `Show more (${total - orders.length} left)`}
            </button>
          </div>
        )}
      </div>
    </main>
  );
}

function OrderCard({ order }) {
  const [expanded, setExpanded] = useState(false);
  const items = expanded ? order.items : order.items.slice(0, ITEMS_PREVIEW);
  const hidden = order.items.length - items.length;
  const isSubscription = order.source === "subscription";

  return (
    <article className="order" aria-labelledby={`order-${order.order_id}`}>
      <header className="order__head">
        <div>
          <h2 className="order__date" id={`order-${order.order_id}`}>
            {formatDate(order.order_date)} <span className="muted">· {formatTime(order.order_datetime)}</span>
          </h2>
          <p className="order__meta">
            <span className="order__id">{order.order_id}</span>
            {order.delivery_slot && <> · Delivery {order.delivery_slot}</>}
            {" · "}{plural(order.unit_count, "item")}
          </p>
        </div>
        <div className="order__right">
          {isSubscription
            ? <a className="order-tag order-tag--subscription" href="#/subscriptions"
                 title="Placed by approving a subscription delivery">Subscription</a>
            : <span className="order-tag">Regular</span>}
          <strong className="order__total">{formatPrice(Number(order.order_total))}</strong>
        </div>
      </header>

      <ul className="order__items">
        {items.map((it) => (
          <li key={it.order_item_id} className="order-item">
            <ProductImage src={it.product.image_url} alt={it.product.product_name} category={it.product.category} />
            <div className="order-item__info">
              <span className="order-item__name">{it.product.product_name}</span>
              <span className="muted">{it.product.pack_size}</span>
            </div>
            <span className="order-item__qty">{it.quantity} × {formatPrice(Number(it.unit_price))}</span>
            <span className="order-item__total">{formatPrice(Number(it.line_total))}</span>
          </li>
        ))}
      </ul>

      {(hidden > 0 || expanded) && order.items.length > ITEMS_PREVIEW && (
        <button type="button" className="link-btn order__toggle" onClick={() => setExpanded((v) => !v)}>
          {expanded ? "Show fewer items" : `Show all ${order.items.length} items`}
        </button>
      )}
    </article>
  );
}
