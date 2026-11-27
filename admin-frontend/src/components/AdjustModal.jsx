import { useState } from "react";
import { adjust } from "../api/admin.js";
import { signed } from "../format.js";
import Modal from "./Modal.jsx";

/** Set an absolute stock count (stock take, damage). A reason is required and logged. */
export default function AdjustModal({ product, stockNow, onClose, onDone }) {
  const [count, setCount] = useState(String(stockNow ?? 0));
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const n = Number(count);
  const valid = count !== "" && Number.isInteger(n) && n >= 0 && n <= 1000000;
  const delta = valid ? n - (stockNow ?? 0) : 0;

  const submit = async (e) => {
    e.preventDefault();
    if (!valid) return setError("Enter a whole number of 0 or more.");
    if (!reason.trim()) return setError("Say why the count is changing.");
    setBusy(true);
    setError(null);
    try {
      onDone(await adjust(product.product_id, n, reason.trim()));
    } catch (err) {
      setError(err.message);
      setBusy(false);
    }
  };

  return (
    <Modal title={`Adjust stock: ${product.product_name}`} onClose={onClose}>
      <form onSubmit={submit} className="form">
        <p className="muted">For corrections such as a stock take or damaged units. To add a delivery, use Restock.</p>
        <label className="field">
          <span>Units actually in stock</span>
          <input type="number" min="0" step="1" inputMode="numeric" value={count} data-autofocus
                 onChange={(e) => setCount(e.target.value)} required />
        </label>
        <p className="muted">Currently {stockNow ?? 0}{valid && delta !== 0 && <> · change <strong>{signed(delta)}</strong></>}</p>
        <label className="field">
          <span>Reason</span>
          <input type="text" maxLength={500} value={reason} placeholder="e.g. stock take, 2 packs damaged"
                 onChange={(e) => setReason(e.target.value)} required />
        </label>
        {error && <p className="form-error" role="alert">{error}</p>}
        <div className="form__actions">
          <button type="button" className="btn btn--ghost" onClick={onClose} disabled={busy}>Cancel</button>
          <button type="submit" className="btn btn--primary" disabled={busy || !valid || delta === 0 || !reason.trim()}>
            {busy ? "Saving…" : "Save count"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
