import { useCallback, useEffect, useState } from "react";
import { getMovements } from "../api/admin.js";
import { formatDateTime, signed } from "../format.js";
import Modal from "./Modal.jsx";
import { Empty, ErrorBox, Loading } from "./States.jsx";

const REASONS = { RESTOCK: "Restock", ADJUST: "Adjustment", ORDER: "Subscription order" };

export default function MovementsDrawer({ product, onClose }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  const load = useCallback(() => {
    setError(null);
    getMovements(product.product_id).then(setData).catch(setError);
  }, [product.product_id]);
  useEffect(load, [load]);

  return (
    <Modal title={`Stock history: ${product.product_name}`} onClose={onClose} wide>
      {error ? <ErrorBox error={error} onRetry={load} />
        : !data ? <Loading />
        : data.movements.length === 0 ? <Empty title="No stock changes yet">Restocks, adjustments and subscription orders show up here.</Empty>
        : (
          <ul className="history">
            {data.movements.map((m) => (
              <li key={m.id} className="history__row">
                <span className={`delta ${m.delta > 0 ? "delta--up" : "delta--down"}`}>{signed(m.delta)}</span>
                <span className="history__what">
                  <strong>{REASONS[m.reason] ?? m.reason}</strong>
                  {m.occurrence_id && <span className="muted"> · delivery #{m.occurrence_id}</span>}
                  {m.note && <span className="history__note">{m.note}</span>}
                </span>
                <time className="muted" dateTime={m.created_at}>{formatDateTime(m.created_at)}</time>
              </li>
            ))}
          </ul>
        )}
      {data?.movements.length === 50 && <p className="muted">Showing the latest 50.</p>}
    </Modal>
  );
}
