import { useState } from "react";
import { delayDelivery, skipDelivery } from "../api/admin.js";
import { addDays, formatDate, plural } from "../format.js";
import Modal from "./Modal.jsx";

/** Delay (1-2 days) or skip a delivery nobody has approved yet, for operational reasons.
 *  The customer gets a Telegram message that cites operational delays; nobody is charged.
 *  `target`: { subscription_id, product_name, customer_label, delivery_date, delay_options }. */
export default function DeliveryActionModal({ mode, target, onClose, onDone }) {
  const options = target.delay_options ?? [];
  const [days, setDays] = useState(options[0] ?? null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const delaying = mode === "delay";
  const date = formatDate(target.delivery_date);
  const newDate = days ? formatDate(addDays(target.delivery_date, days)) : null;

  const message = delaying
    ? `Due to operational delays, your ${target.product_name} delivery for ${date} has moved to ${newDate ?? "…"}. `
      + "You have not been charged. We will send you a reminder to approve it before then."
    : `Due to operational delays, we can't deliver your ${target.product_name} on ${date}, so this delivery has `
      + "been skipped. You have not been charged. Your next delivery is scheduled for <next delivery date>.";

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      onDone(delaying ? await delayDelivery(target.subscription_id, days) : await skipDelivery(target.subscription_id));
    } catch (err) {
      setError(err.message);
      setBusy(false);
    }
  };

  return (
    <Modal title={`${delaying ? "Delay" : "Skip"} ${target.product_name} delivery`} onClose={onClose}>
      <form onSubmit={submit} className="form">
        <p className="muted">{target.customer_label} · delivery on <strong>{date}</strong></p>
        {delaying && (options.length === 0 ? (
          <p className="form-error">This delivery has already been delayed as far as it can be (2 days in total).</p>
        ) : (
          <fieldset className="choices">
            <legend>Delay by</legend>
            {options.map((n) => (
              <label key={n} className="choice">
                <input type="radio" name="days" value={n} checked={days === n} data-autofocus={n === options[0] || undefined}
                       onChange={() => setDays(n)} />
                <span>{plural(n, "day")} <span className="muted">→ {formatDate(addDays(target.delivery_date, n))}</span></span>
              </label>
            ))}
          </fieldset>
        ))}
        <div>
          <p className="small muted">Telegram message to the customer:</p>
          <blockquote className="message-preview">{message}</blockquote>
        </div>
        <p className="hint">
          {delaying
            ? "The customer must approve again: a fresh reminder goes out the day before the new date."
            : "The subscription moves to its next delivery. This can't be undone."}
        </p>
        {error && <p className="form-error" role="alert">{error}</p>}
        <div className="form__actions">
          <button type="button" className="btn btn--ghost" onClick={onClose} disabled={busy}>Cancel</button>
          <button type="submit" className={`btn ${delaying ? "btn--primary" : "btn--danger"}`}
                  disabled={busy || (delaying && !days)}>
            {busy ? "Saving…" : delaying ? `Delay to ${newDate ?? "…"}` : "Skip delivery"}
          </button>
        </div>
      </form>
    </Modal>
  );
}

/** One-line result for the page's notice. */
export function describeResult(r, productName) {
  let text = r.action === "delayed"
    ? `${productName} delivery moved from ${formatDate(r.delivery_date)} to ${formatDate(r.new_delivery_date)}.`
    : `${productName} delivery on ${formatDate(r.delivery_date)} skipped; next is ${formatDate(r.next_delivery_date)}.`;
  text += r.customer_notified ? " Customer told on Telegram." : " The customer can't be messaged, so wasn't told.";
  if (r.holds?.released) text += ` Freed stock sent ${plural(r.holds.released, "held reminder")}.`;
  return { text, tone: r.customer_notified ? "ok" : "warn" };
}
