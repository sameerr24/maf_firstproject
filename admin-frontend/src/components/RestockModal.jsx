import { useState } from "react";
import { restock } from "../api/admin.js";
import { plural } from "../format.js";
import Modal from "./Modal.jsx";

const MAX = 10000;

/** Add stock. `suggested` pre-fills the quantity (e.g. the shortfall on the At-risk page). */
export default function RestockModal({ product, stockNow, suggested, onClose, onDone }) {
  const [quantity, setQuantity] = useState(String(Math.min(Math.max(suggested ?? 1, 1), MAX)));
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const n = Number(quantity);
  const valid = Number.isInteger(n) && n >= 1 && n <= MAX;

  const submit = async (e) => {
    e.preventDefault();
    if (!valid) return setError(`Enter a whole number from 1 to ${MAX}.`);
    setBusy(true);
    setError(null);
    try {
      onDone(await restock(product.product_id, n, note.trim()));
    } catch (err) {
      setError(err.message);
      setBusy(false);
    }
  };

  return (
    <Modal title={`Restock ${product.product_name}`} onClose={onClose}>
      <form onSubmit={submit} className="form">
        <p className="muted">{product.brand} · {product.pack_size} · {plural(stockNow ?? 0, "unit")} in stock now</p>
        <label className="field">
          <span>Units to add</span>
          <input type="number" min="1" max={MAX} step="1" inputMode="numeric" value={quantity} data-autofocus
                 onChange={(e) => setQuantity(e.target.value)} required />
        </label>
        {valid && <p className="muted">New stock: <strong>{(stockNow ?? 0) + n}</strong></p>}
        <label className="field">
          <span>Note <span className="muted">(optional)</span></span>
          <input type="text" maxLength={500} value={note} placeholder="e.g. supplier delivery"
                 onChange={(e) => setNote(e.target.value)} />
        </label>
        {suggested > 0 && (
          <p className="hint">Held deliveries the new stock covers get their reminder right away.</p>
        )}
        {error && <p className="form-error" role="alert">{error}</p>}
        <div className="form__actions">
          <button type="button" className="btn btn--ghost" onClick={onClose} disabled={busy}>Cancel</button>
          <button type="submit" className="btn btn--primary" disabled={busy || !valid}>
            {busy ? "Adding…" : `Add ${valid ? plural(n, "unit") : "stock"}`}
          </button>
        </div>
      </form>
    </Modal>
  );
}
