import { useCallback, useEffect, useState } from "react";
import { getSubscriptions } from "../api/admin.js";
import ProductThumb from "../components/ProductThumb.jsx";
import { Empty, ErrorBox, Loading } from "../components/States.jsx";
import { formatDate, formatPrice } from "../format.js";
import { toHash } from "../router.js";

const PAGE_SIZE = 25;
const OPEN = {
  STOCK_HOLD: ["chip--short", "Held for stock"],
  NOTIFIED: ["chip--info", "Awaiting reply"],
  DELAYED: ["chip--muted", "Delayed"],
};

/** Read-only search over active and paused subscriptions. Filters live in the URL hash, so
 *  links from the other pages (a product's subscribers, a held customer) open filtered. */
export default function SubscriptionsPage({ params, navigate }) {
  const { q: urlQ = "", status = "", product_id: productId = "", customer_id: customerId = "" } = params;
  const page = Math.max(1, Number(params.page) || 1);
  const [q, setQ] = useState(urlQ);
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);

  const setFilters = useCallback((changes) => {
    navigate("subscriptions", { q: urlQ, status, product_id: productId, customer_id: customerId, ...changes,
                                page: changes.page ?? undefined });
  }, [navigate, urlQ, status, productId, customerId]);

  useEffect(() => { setQ(urlQ); }, [urlQ]);
  useEffect(() => {                                     // debounce typing into the URL
    if (q.trim() === urlQ) return undefined;
    const t = setTimeout(() => setFilters({ q: q.trim() }), 300);
    return () => clearTimeout(t);
  }, [q, urlQ, setFilters]);

  const load = useCallback(() => {
    setLoading(true);
    return getSubscriptions({ q: urlQ, status, product_id: productId, customer_id: customerId, page, page_size: PAGE_SIZE })
      .then((d) => { setData(d); setError(null); }).catch(setError).finally(() => setLoading(false));
  }, [urlQ, status, productId, customerId, page]);
  useEffect(() => { load(); }, [load]);

  const first = data ? (data.page - 1) * data.page_size + 1 : 0;
  const last = data ? Math.min(data.page * data.page_size, data.total) : 0;
  const filterLabel = (row, kind) => kind === "product"
    ? row?.product.product_name : (row?.customer.customer_name || row?.customer.customer_id);
  const sample = data?.items[0];

  return (
    <section aria-labelledby="subs-title">
      <div className="page-head">
        <div>
          <h1 id="subs-title">Subscriptions</h1>
          <p className="muted">Active and paused subscriptions. Click a product or a customer to filter by it.</p>
        </div>
      </div>

      <div className="toolbar">
        <input type="search" className="search" value={q} onChange={(e) => setQ(e.target.value)}
               placeholder="Search product, brand, customer, ID or phone digits" aria-label="Search subscriptions" />
        <select value={status} onChange={(e) => setFilters({ status: e.target.value })} aria-label="Status">
          <option value="">Active and paused</option>
          <option value="ACTIVE">Active</option>
          <option value="PAUSED">Paused</option>
        </select>
      </div>
      {(productId || customerId) && (
        <div className="filters">
          {productId && (
            <button type="button" className="filter-chip" onClick={() => setFilters({ product_id: "" })}>
              Product: {(productId && sample?.product.product_id === productId && filterLabel(sample, "product")) || productId} ×
            </button>
          )}
          {customerId && (
            <button type="button" className="filter-chip" onClick={() => setFilters({ customer_id: "" })}>
              Customer: {(customerId && sample?.customer.customer_id === customerId && filterLabel(sample, "customer")) || customerId} ×
            </button>
          )}
        </div>
      )}

      {error && !data ? <ErrorBox error={error} onRetry={load} />
        : !data ? <Loading />
        : data.items.length === 0 ? <Empty title="No subscriptions match">Try another search or clear a filter.</Empty>
        : (
          <>
            <div className={`table-wrap card${loading ? " is-loading" : ""}`}>
              <table className="table">
                <thead>
                  <tr>
                    <th>Customer</th><th>Product</th><th>Plan</th><th>Next delivery</th>
                    <th>Price</th><th>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((s) => {
                    const open = OPEN[s.open_delivery];
                    return (
                      <tr key={s.subscription_id} className={s.at_risk ? "row--risk" : ""}>
                        <td>
                          <button type="button" className="link" onClick={() => setFilters({ customer_id: s.customer.customer_id })}>
                            {s.customer.customer_name || s.customer.customer_id}
                          </button>
                          <div className="muted small">{s.customer.customer_id} · {s.customer.phone}</div>
                        </td>
                        <td>
                          <div className="product-cell">
                            <ProductThumb product={s.product} size={36} />
                            <div>
                              <button type="button" className="link" onClick={() => setFilters({ product_id: s.product.product_id })}>
                                {s.product.product_name}
                              </button>
                              <div className="muted small">{s.product.brand} · {s.product.pack_size}</div>
                            </div>
                          </div>
                        </td>
                        <td>{s.quantity} every {s.frequency_days} {s.frequency_days === 1 ? "day" : "days"}</td>
                        <td>
                          {s.status === "PAUSED"
                            ? <span className="muted">Paused{s.paused_until ? ` until ${formatDate(s.paused_until)}` : ""}</span>
                            : formatDate(s.next_delivery_date)}
                          {s.preferred_slot && <div className="muted small">{s.preferred_slot}</div>}
                        </td>
                        <td>
                          {formatPrice(s.current_price)}
                          {s.price_changed && <div className="muted small">was {formatPrice(s.unit_price_at_subscription)}</div>}
                        </td>
                        <td>
                          <span className={`chip ${s.status === "ACTIVE" ? "chip--ok" : "chip--muted"}`}>{s.status === "ACTIVE" ? "Active" : "Paused"}</span>
                          {open && <span className={`chip ${open[0]}`}>{open[1]}</span>}
                          {s.at_risk && <div className="small"><a href={toHash("at-risk")}>See at-risk</a></div>}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <nav className="pager" aria-label="Pages">
              <span className="muted">{first}–{last} of {data.total}</span>
              <button type="button" className="btn btn--ghost btn--small" disabled={data.page <= 1}
                      onClick={() => setFilters({ page: data.page - 1 })}>Previous</button>
              <button type="button" className="btn btn--ghost btn--small" disabled={last >= data.total}
                      onClick={() => setFilters({ page: data.page + 1 })}>Next</button>
            </nav>
          </>
        )}
      {error && data && <p className="form-error" role="alert">Couldn't refresh: {error.message}</p>}
    </section>
  );
}
