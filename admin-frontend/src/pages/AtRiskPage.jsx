import { useCallback, useState } from "react";
import { getAtRisk } from "../api/admin.js";
import ProductThumb from "../components/ProductThumb.jsx";
import DeliveryActionModal, { describeResult } from "../components/DeliveryActionModal.jsx";
import DeliveryButtons from "../components/DeliveryButtons.jsx";
import RestockModal from "../components/RestockModal.jsx";
import { Empty, ErrorBox, Loading, Notice } from "../components/States.jsx";
import { formatDate, formatDateTime, plural, timeUntil } from "../format.js";
import { toHash } from "../router.js";
import { usePolling } from "../usePolling.js";

const POLL_MS = 10000;

/** Deliveries held because the stock can't cover them. Nobody has been told yet: restock
 *  before the hold time and the reminder goes out; otherwise the delivery is skipped. */
export default function AtRiskPage({ onChanged }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [restocking, setRestocking] = useState(null);     // product group
  const [notice, setNotice] = useState(null);
  const [action, setAction] = useState(null);             // { mode: "delay" | "skip", target }

  const load = useCallback(() => getAtRisk().then((d) => { setData(d); setError(null); onChanged?.(d.total); })
    .catch(setError), [onChanged]);
  usePolling(load, POLL_MS);

  const restocked = (group, result) => {
    setRestocking(null);
    const { released, out_of_stock: expired, status } = result.holds;
    let text = `Added ${plural(result.added, "unit")} of ${group.product.product_name} (now ${result.available_qty}).`;
    if (released) text += ` ${plural(released, "reminder")} sent.`;
    if (expired) text += ` ${plural(expired, "delivery")} past the hold time ${expired === 1 ? "was" : "were"} skipped.`;
    if (status === "failed") text += " Reminders couldn't be sent now; the next scheduler run will send them.";
    setNotice({ text, tone: status === "failed" ? "warn" : "ok" });
    load();
  };

  const acted = (result) => {
    setNotice(describeResult(result, action.target.product_name));
    setAction(null);
    load();
  };

  return (
    <section aria-labelledby="at-risk-title">
      <div className="page-head">
        <div>
          <h1 id="at-risk-title">At-risk deliveries</h1>
          <p className="muted">Deliveries held because there isn't enough stock. The customer hasn't been told yet:
            restock before the hold ends and their usual reminder goes out, or delay or skip a delivery
            (the customer is told it's due to operational delays).</p>
        </div>
      </div>
      <Notice notice={notice} onClose={() => setNotice(null)} />

      {error && !data ? <ErrorBox error={error} onRetry={load} />
        : !data ? <Loading />
        : data.products.length === 0 ? (
          <Empty title="Nothing at risk">
            When a reminder can't go out because of stock, the delivery is held and shows up here.
          </Empty>
        ) : data.products.map((g) => (
          <ProductGroup key={g.product.product_id} group={g} onRestock={() => setRestocking(g)}
                        onAction={(mode, target) => setAction({ mode, target })} />
        ))}
      {error && data && <p className="form-error" role="alert">Couldn't refresh: {error.message}</p>}

      {restocking && (
        <RestockModal product={restocking.product} stockNow={restocking.stock_now}
                      suggested={restocking.add_to_release_all}
                      onClose={() => setRestocking(null)} onDone={(r) => restocked(restocking, r)} />
      )}
      {action && (
        <DeliveryActionModal mode={action.mode} target={action.target}
                             onClose={() => setAction(null)} onDone={acted} />
      )}
    </section>
  );
}

function ProductGroup({ group: g, onRestock, onAction }) {
  const p = g.product;
  const until = g.earliest_hold_until;
  return (
    <article className="card group" aria-labelledby={`g-${p.product_id}`}>
      <header className="group__head">
        <ProductThumb product={p} size={56} />
        <div className="group__title">
          <h2 id={`g-${p.product_id}`}>{p.product_name} <span className="muted">· {p.brand}, {p.pack_size}</span></h2>
          <p>
            <strong>{g.stock_now}</strong> in stock
            {g.promised > 0 && <>, {g.promised} promised to open reminders</>}
            {" · "}<strong>{g.held_qty}</strong> needed by {formatDate(g.earliest_delivery_date)}
            {g.add_to_release_all > 0
              ? <> → <span className="danger">add {g.add_to_release_all}</span></>
              : <> → <span className="ok-text">covered, releasing on the next run</span></>}
          </p>
          <p className="muted">
            Customers are told the delivery is skipped at <strong>{formatDateTime(until)}</strong>
            {" "}({timeUntil(until)}) unless it's restocked.
          </p>
        </div>
        <button type="button" className="btn btn--primary" onClick={onRestock}>Restock</button>
      </header>
      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr><th>Delivery</th><th>Customer</th><th className="num">Qty</th><th>Slot</th><th>Hold ends</th><th><span className="sr-only">Actions</span></th></tr>
          </thead>
          <tbody>
            {g.lines.map((l) => (
              <tr key={l.occurrence_id}>
                <td>{formatDate(l.delivery_date)}</td>
                <td>
                  <a href={toHash("subscriptions", { customer_id: l.customer.customer_id })}>
                    {l.customer.customer_name || l.customer.customer_id}
                  </a>
                  <div className="muted small">{l.customer.customer_id} · {l.customer.phone}</div>
                </td>
                <td className="num">{l.quantity}</td>
                <td>{l.preferred_slot ?? <span className="muted">-</span>}</td>
                <td>
                  {l.expired
                    ? <span className="chip chip--short">Skipped on next run</span>
                    : <>{formatDateTime(l.hold_until)}<div className="muted small">{timeUntil(l.hold_until)}</div></>}
                </td>
                <td>
                  {!l.expired && (
                    <DeliveryButtons onAction={onAction} target={{
                      subscription_id: l.subscription_id, product_name: p.product_name,
                      customer_label: l.customer.customer_name || l.customer.customer_id,
                      delivery_date: l.delivery_date, delay_options: l.delay_options,
                    }} />
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </article>
  );
}
