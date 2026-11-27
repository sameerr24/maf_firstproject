import { useCallback, useEffect, useState } from "react";
import { getInventory } from "../api/admin.js";
import AdjustModal from "../components/AdjustModal.jsx";
import MovementsDrawer from "../components/MovementsDrawer.jsx";
import ProductThumb from "../components/ProductThumb.jsx";
import RestockModal from "../components/RestockModal.jsx";
import { Empty, ErrorBox, Loading, Notice, StockChip } from "../components/States.jsx";
import { formatDateTime, plural, signed } from "../format.js";
import { toHash } from "../router.js";

const FILTERS = [["", "All"], ["short", "Short"], ["low", "Low"], ["ok", "OK"]];

export default function InventoryPage({ onChanged }) {
  const [q, setQ] = useState("");
  const [query, setQuery] = useState("");            // debounced q
  const [status, setStatus] = useState("");
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);
  const [dialog, setDialog] = useState(null);        // {kind: restock|adjust|history, item}
  const [notice, setNotice] = useState(null);

  useEffect(() => {
    const t = setTimeout(() => setQuery(q.trim()), 250);
    return () => clearTimeout(t);
  }, [q]);

  const load = useCallback(() => {
    setLoading(true);
    return getInventory({ q: query, status }).then((d) => { setData(d); setError(null); })
      .catch(setError).finally(() => setLoading(false));
  }, [query, status]);
  useEffect(() => { load(); }, [load]);

  const done = (item, text, holds) => {
    setDialog(null);
    let full = text;
    if (holds?.released) full += ` ${plural(holds.released, "held delivery")} released.`;
    setNotice({ text: full });
    load();
    onChanged?.();
  };

  return (
    <section aria-labelledby="inventory-title">
      <div className="page-head">
        <div>
          <h1 id="inventory-title">Inventory</h1>
          <p className="muted">Stock per product against open demand: reminders awaiting a reply plus held deliveries.</p>
        </div>
      </div>
      <Notice notice={notice} onClose={() => setNotice(null)} />

      <div className="toolbar">
        <input type="search" className="search" placeholder="Search product, brand or ID" value={q}
               onChange={(e) => setQ(e.target.value)} aria-label="Search products" />
        <div className="segmented" role="group" aria-label="Filter by status">
          {FILTERS.map(([value, label]) => (
            <button key={value} type="button" aria-pressed={status === value}
                    className={status === value ? "is-active" : ""} onClick={() => setStatus(value)}>
              {label}{value && data ? ` ${data.counts[value]}` : ""}
            </button>
          ))}
        </div>
      </div>

      {error && !data ? <ErrorBox error={error} onRetry={load} />
        : !data ? <Loading />
        : data.items.length === 0 ? <Empty title="No products match">Try another search or filter.</Empty>
        : (
          <div className={`table-wrap card${loading ? " is-loading" : ""}`}>
            <table className="table">
              <thead>
                <tr>
                  <th>Product</th><th className="num">In stock</th><th className="num">Promised</th>
                  <th className="num">Held</th><th className="num">Left</th><th>Status</th>
                  <th className="num">Subscribers</th><th>Updated</th><th><span className="visually-hidden">Actions</span></th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((i) => (
                  <tr key={i.product.product_id}>
                    <td>
                      <div className="product-cell">
                        <ProductThumb product={i.product} />
                        <div>
                          <div>{i.product.product_name}</div>
                          <div className="muted small">{i.product.brand} · {i.product.pack_size} · {i.product.product_id}</div>
                        </div>
                      </div>
                    </td>
                    <td className="num"><strong>{i.stock_now}</strong></td>
                    <td className="num">{i.promised || <span className="muted">0</span>}</td>
                    <td className="num">{i.held_qty ? <span className="danger">{i.held_qty}</span> : <span className="muted">0</span>}</td>
                    <td className={`num${i.left_after_demand < 0 ? " danger" : ""}`}>{i.left_after_demand}</td>
                    <td><StockChip status={i.status} /></td>
                    <td className="num">
                      {i.active_subscriptions
                        ? <a href={toHash("subscriptions", { product_id: i.product.product_id })}>{i.active_subscriptions}</a>
                        : <span className="muted">0</span>}
                    </td>
                    <td className="small">{i.updated_at ? formatDateTime(i.updated_at) : <span className="muted">never</span>}</td>
                    <td>
                      <div className="row-actions">
                        <button type="button" className="btn btn--small btn--primary" onClick={() => setDialog({ kind: "restock", item: i })}>Restock</button>
                        <button type="button" className="btn btn--small btn--ghost" onClick={() => setDialog({ kind: "adjust", item: i })}>Adjust</button>
                        <button type="button" className="btn btn--small btn--ghost" onClick={() => setDialog({ kind: "history", item: i })}>History</button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      {error && data && <p className="form-error" role="alert">Couldn't refresh: {error.message}</p>}

      {dialog?.kind === "restock" && (
        <RestockModal product={dialog.item.product} stockNow={dialog.item.stock_now}
                      suggested={Math.max(1, -dialog.item.left_after_demand)}
                      onClose={() => setDialog(null)}
                      onDone={(r) => done(dialog.item, `Added ${plural(r.added, "unit")} of ${dialog.item.product.product_name} (now ${r.available_qty}).`, r.holds)} />
      )}
      {dialog?.kind === "adjust" && (
        <AdjustModal product={dialog.item.product} stockNow={dialog.item.stock_now} onClose={() => setDialog(null)}
                     onDone={(r) => done(dialog.item, `${dialog.item.product.product_name} set to ${r.available_qty} (${signed(r.delta)}).`, r.holds)} />
      )}
      {dialog?.kind === "history" && <MovementsDrawer product={dialog.item.product} onClose={() => setDialog(null)} />}
    </section>
  );
}
