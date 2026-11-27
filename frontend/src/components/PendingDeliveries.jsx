import { useCallback, useEffect, useState } from "react";
import { PENDING_EVENT } from "./PendingBadge.jsx";
import ProductImage from "./ProductImage.jsx";
import {
  approveDelivery, cancelSubscription, delayDelivery, getPendingDeliveries, skipDelivery,
} from "../api/subscriptions.js";
import { formatDate, formatPrice, plural } from "../format.js";

// Delivery reminders waiting for a reply (subscriptions module). The same reminder is sent in
// chat (Telegram in the demo, WhatsApp in the product) with the same options: Approve (pick a
// slot), Skip or delay, Stop subscription. Answering here also clears the chat's buttons and
// posts the confirmation there; answering in chat makes the reminder disappear here.

const POLL_MS = 15000;

/** "9:00 PM, 27 Nov" in IST from an ISO timestamp with offset. */
function formatReplyBy(iso) {
  const d = new Date(iso);
  const time = d.toLocaleTimeString("en-IN", { hour: "numeric", minute: "2-digit", hour12: true, timeZone: "Asia/Kolkata" });
  const day = d.toLocaleDateString("en-IN", { weekday: "short", day: "numeric", month: "short", timeZone: "Asia/Kolkata" });
  return `${time.toUpperCase()} on ${day}`;
}

function addDays(isoDate, n) {
  const [y, m, d] = isoDate.split("-").map(Number);
  const date = new Date(Date.UTC(y, m - 1, d + n));
  return date.toISOString().slice(0, 10);
}

export default function PendingDeliveries({ token, onChanged, onFail }) {
  const [deliveries, setDeliveries] = useState([]);
  const [done, setDone] = useState([]);       // confirmations of replies made on this page

  const load = useCallback(
    () => getPendingDeliveries(token).then((d) => {
      setDeliveries(d.deliveries);
      window.dispatchEvent(new CustomEvent(PENDING_EVENT, { detail: d.deliveries.length }));   // header badge
    }).catch((e) => {
      if (e.status === 401) onFail(e);        // other errors: keep what's shown, try again next poll
    }),
    [token, onFail]);

  // Poll, and re-check when the tab regains focus, so a reminder sent by the scheduler (or
  // answered in chat) shows up here without a reload.
  useEffect(() => {
    load();
    const timer = setInterval(load, POLL_MS);
    const onVisible = () => document.visibilityState === "visible" && load();
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [load]);

  const answered = (occurrenceId, message) => {
    setDeliveries((list) => list.filter((d) => d.occurrence_id !== occurrenceId));
    if (message) setDone((list) => [{ id: occurrenceId, ...message }, ...list]);
    load();
    onChanged?.();
  };

  if (deliveries.length === 0 && done.length === 0) return null;

  return (
    <section className="pending" aria-labelledby="pending-title">
      {deliveries.length > 0 && (
        <div className="pending__head">
          <h2 id="pending-title">
            Waiting for your reply <span className="pending__count">{deliveries.length}</span>
          </h2>
          <p className="muted">Also sent to you in chat. Answer here or there, whichever is easier.</p>
        </div>
      )}
      {done.map((m) => (
        <div key={m.id} className={`pending-done${m.tone === "warn" ? " pending-done--warn" : ""}`} role="status">
          <span>{m.text}</span>
          {m.link && <a href={m.link.href} className="link-btn">{m.link.label}</a>}
          <button type="button" className="link-btn pending-done__close" aria-label="Dismiss"
                  onClick={() => setDone((list) => list.filter((x) => x.id !== m.id))}>Dismiss</button>
        </div>
      ))}
      {deliveries.map((d) => (
        <PendingDelivery key={d.occurrence_id} delivery={d} token={token} onFail={onFail}
                         onAnswered={answered} onStale={load} />
      ))}
    </section>
  );
}

function PendingDelivery({ delivery: d, token, onFail, onAnswered, onStale }) {
  const [step, setStep] = useState("menu");                 // menu | approve | skip | stop
  const [slot, setSlot] = useState(d.preferred_slot ?? d.slots[0]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const p = d.product;
  const when = formatDate(d.delivery_date);

  const run = (promise, message) => {
    setBusy(true);
    setError(null);
    promise
      .then((r) => onAnswered(d.occurrence_id, message(r)))
      .catch((e) => {
        if (e.status === 401) return onFail(e);
        setError(e.message);
        if (e.status === 409 || e.status === 404) onStale();  // answered in chat or expired meanwhile
      })
      .finally(() => setBusy(false));
  };

  const approve = () => run(approveDelivery(token, d.occurrence_id, slot), (r) => (
    r.status === "order_placed"
      ? { text: `Order placed: ${r.quantity} × ${p.product_name} on ${when}, ${r.slot}. ${formatPrice(Number(r.total))} · ${r.order_id}.`,
          link: { href: "#/orders", label: "View order" } }
      : { tone: "warn",
          text: `Sorry, ${p.product_name} just went out of stock, so this delivery wasn't placed. You have not been charged.` }));
  const skip = () => run(skipDelivery(token, d.occurrence_id), (r) => ({
    text: `Skipped your ${when} delivery of ${p.product_name}. You have not been charged.`
      + (r.next_delivery_date ? ` Next delivery: ${formatDate(r.next_delivery_date)}.` : ""),
  }));
  const delay = (n) => run(delayDelivery(token, d.occurrence_id, n), (r) => ({
    text: `Moved your ${p.product_name} delivery to ${formatDate(r.delivery_date)}. We'll ask you to approve it again `
      + `on ${formatDate(addDays(r.delivery_date, -1))}; nothing is charged until you approve.`,
  }));
  const stop = () => run(cancelSubscription(token, d.subscription_id), () => ({
    text: `Your ${p.product_name} subscription is cancelled. You won't be charged again.`,
  }));

  return (
    <article className="pending-card" aria-labelledby={`pending-${d.occurrence_id}`}>
      <div className="pending-card__media">
        <ProductImage src={p.image_url} alt={p.product_name} category={p.category} />
      </div>
      <div className="pending-card__body">
        <p className="eyebrow">Delivery reminder</p>
        <h3 id={`pending-${d.occurrence_id}`} className="pending-card__title">
          {d.quantity} × {p.product_name} <span className="muted">· {when}</span>
        </h3>
        <p className="pending-card__text">
          Total <strong>{formatPrice(Number(d.total))}</strong>
          {Number(d.subscription_fee) > 0 && <> (includes one-time fee {formatPrice(Number(d.subscription_fee))})</>}
          {Number(d.saving) > 0 && <> (you save <span className="accent">{formatPrice(Number(d.saving))}</span>)</>}
          {d.price_changed && <> · price changed, was {formatPrice(Number(d.previous_total))}</>}
          {d.delayed_days > 0 && <> · delayed by {plural(d.delayed_days, "day")}</>}
        </p>
        <p className="muted pending-card__note">
          You won't be charged unless you approve.
          {Number(d.subscription_fee) > 0 && <> The one-time fee is recorded in the order total; no payment is collected in this demo.</>}
          {d.reply_by && <> If we don't hear from you by <strong>{formatReplyBy(d.reply_by)}</strong>, this delivery will be skipped.</>}
        </p>

        {error && <p className="form-error" role="alert">{error}</p>}

        {step === "menu" && (
          <div className="pending-card__actions">
            <button type="button" className="btn btn--primary btn--sm" disabled={busy} onClick={() => setStep("approve")}>Approve</button>
            <button type="button" className="btn btn--ghost btn--sm" disabled={busy} onClick={() => setStep("skip")}>Skip or delay</button>
            <button type="button" className="btn btn--ghost btn--sm sub-card__cancel" disabled={busy} onClick={() => setStep("stop")}>
              Stop subscription
            </button>
          </div>
        )}

        {step === "approve" && (
          <fieldset className="pending-card__step" disabled={busy}>
            <legend>Pick a delivery slot for {when}</legend>
            <div className="pending-card__slots" role="radiogroup">
              {d.slots.map((s) => (
                <button key={s} type="button" role="radio" aria-checked={slot === s}
                        className={`chip${slot === s ? " chip--active" : ""}`} onClick={() => setSlot(s)}>{s}</button>
              ))}
            </div>
            <div className="pending-card__actions">
              <button type="button" className="btn btn--primary btn--sm" onClick={approve}>
                {busy ? "Placing order…" : `Place order · ${formatPrice(Number(d.total))}`}
              </button>
              <button type="button" className="btn btn--ghost btn--sm" onClick={() => setStep("menu")}>Back</button>
            </div>
          </fieldset>
        )}

        {step === "skip" && (
          <fieldset className="pending-card__step" disabled={busy}>
            <legend>What would you like to do with your {when} delivery?</legend>
            <div className="pending-card__actions">
              <button type="button" className="btn btn--ghost btn--sm" onClick={skip}>Skip this delivery</button>
              {d.delay_options.map((n) => (
                <button key={n} type="button" className="btn btn--ghost btn--sm" onClick={() => delay(n)}>
                  Delay {plural(n, "day")} <span className="muted">→ {formatDate(addDays(d.delivery_date, n))}</span>
                </button>
              ))}
              <button type="button" className="link-btn" onClick={() => setStep("menu")}>Back</button>
            </div>
          </fieldset>
        )}

        {step === "stop" && (
          <fieldset className="pending-card__step" disabled={busy}>
            <legend>Stop your {p.product_name} subscription? All future deliveries will be cancelled.</legend>
            <div className="pending-card__actions">
              <button type="button" className="btn btn--ghost btn--sm sub-card__cancel" onClick={stop}>Yes, stop it</button>
              <button type="button" className="btn btn--ghost btn--sm" onClick={() => setStep("menu")}>No, keep it</button>
            </div>
          </fieldset>
        )}
      </div>
    </article>
  );
}
