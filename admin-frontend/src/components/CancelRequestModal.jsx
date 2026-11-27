import { useState } from "react";
import { requestCancellation, withdrawCancellation } from "../api/admin.js";
import { formatDateTime, frequencyText, plural } from "../format.js";
import Modal from "./Modal.jsx";

/** Ask the customer to confirm cancelling a subscription, or withdraw that request.
 *  `sub` is a row of the Subscriptions page. */
export default function CancelRequestModal({ mode, sub, onClose, onDone }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const asking = mode === "request";
  const product = sub.product.product_name;
  const customer = sub.customer.customer_name || sub.customer.customer_id;

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      onDone(asking ? await requestCancellation(sub.subscription_id) : await withdrawCancellation(sub.subscription_id));
    } catch (err) {
      setError(err.message);
      setBusy(false);
    }
  };

  return (
    <Modal title={asking ? `Ask to cancel ${product}?` : "Withdraw the cancellation request?"} onClose={onClose}>
      <form onSubmit={submit} className="form">
        <p className="muted">{customer} · {sub.quantity} {frequencyText(sub.frequency_days)}</p>
        {asking ? (
          <>
            <div>
              <p className="small muted">Telegram message to the customer (also shown on their dashboard):</p>
              <blockquote className="message-preview">
                <strong>Please confirm</strong>{"\n\n"}
                Due to operational reasons, we may not be able to continue your {product} subscription
                {" "}({sub.quantity} {frequencyText(sub.frequency_days)}). Would you like to cancel it?{"\n\n"}
                Until you reply, we won't schedule any deliveries. You won't be charged for anything you
                haven't approved.
                <span className="preview-buttons"><span>Yes, cancel it</span><span>Keep my subscription</span></span>
              </blockquote>
            </div>
            <p className="hint">
              Only the customer can cancel. Until they answer, any delivery not yet approved is cancelled and no
              reminders go out.
            </p>
          </>
        ) : (
          <p>
            Requested {formatDateTime(sub.cancel_request.requested_at)}. The customer's buttons are removed, they're
            told the subscription continues, and reminders resume.
          </p>
        )}
        {error && <p className="form-error" role="alert">{error}</p>}
        <div className="form__actions">
          <button type="button" className="btn btn--ghost" onClick={onClose} disabled={busy}>Back</button>
          <button type="submit" className={`btn ${asking ? "btn--danger" : "btn--primary"}`} disabled={busy} data-autofocus>
            {busy ? "Sending…" : asking ? "Send request" : "Withdraw request"}
          </button>
        </div>
      </form>
    </Modal>
  );
}

/** One-line result for the page's notice. */
export function describeCancelResult(r, mode, productName) {
  if (mode === "withdraw") return { text: `Request withdrawn: the ${productName} subscription continues.`, tone: "ok" };
  let text = `Cancellation request sent for ${productName}.`;
  text += r.customer_notified ? " The customer was asked on Telegram and on their dashboard."
    : " The customer can't be messaged; they'll see it on their dashboard.";
  if (r.cancelled_deliveries.length) text += ` ${plural(r.cancelled_deliveries.length, "open delivery")} cancelled.`;
  if (r.holds?.released) text += ` Freed stock sent ${plural(r.holds.released, "held reminder")}.`;
  return { text, tone: r.customer_notified ? "ok" : "warn" };
}
