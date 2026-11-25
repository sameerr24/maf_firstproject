import { useCallback, useEffect, useState } from "react";
import PendingDeliveries from "../components/PendingDeliveries.jsx";
import ProductImage from "../components/ProductImage.jsx";
import {
  cancelSubscription, createSubscription, getDraft, listSubscriptions, skipNextDelivery, updateSubscription,
} from "../api/subscriptions.js";
import { formatDate, formatPrice, plural } from "../format.js";
import "../styles/subscriptions.css";

// Subscription page (subscriptions module, docs/SUBSCRIPTIONS_CONTEXT.md):
//   1. a card for the new subscription, pre-filled with the engine's recommended quantity
//      and frequency (editable), for the product chosen on the recommendations page;
//   2. the customer's existing subscriptions, each with edit, skip next, pause/resume, cancel.
// Every delivery is approved by the customer in chat before anything is charged.

const CHAT_BOT_URL = import.meta.env.VITE_CHAT_BOT_URL ?? "https://t.me/Subscribe_Maf_Bot";
const clamp = (n, [lo, hi]) => Math.min(hi, Math.max(lo, n));
const toInt = (value) => (/^\d+$/.test(String(value).trim()) ? Number(value) : NaN);
const inRange = (n, [lo, hi]) => Number.isInteger(n) && n >= lo && n <= hi;

function recommendedEvery(rec) {
  return rec?.expected_purchase_interval == null ? null : Math.max(1, Math.round(rec.expected_purchase_interval));
}

export default function SubscriptionPage({ product, session, onSessionExpired, onBack }) {
  const token = session?.token;
  const productId = product?.product_id;
  const [draft, setDraft] = useState(null);
  const [subs, setSubs] = useState(null);
  const [error, setError] = useState(null);
  const [justCreated, setJustCreated] = useState(null);

  // 401 means the token expired or is invalid: sign in again (App shows the login page).
  const fail = useCallback((e) => {
    if (e.status === 401) {
      onSessionExpired();
      return;
    }
    setError(e.message);
  }, [onSessionExpired]);

  const loadSubs = useCallback(
    () => listSubscriptions(token).then((d) => setSubs(d.subscriptions)).catch(fail), [token, fail]);
  const loadDraft = useCallback(
    () => (productId ? getDraft(token, productId).then((d) => setDraft(d.draft)).catch(fail) : Promise.resolve()),
    [token, productId, fail]);

  useEffect(() => {
    setError(null);
    loadSubs();
    loadDraft();
  }, [loadSubs, loadDraft]);

  const refresh = useCallback(() => Promise.all([loadSubs(), loadDraft()]), [loadSubs, loadDraft]);

  const onCreated = (sub) => {
    setJustCreated(sub);
    refresh();
  };

  const existingId = draft?.existing_subscription?.subscription_id ?? justCreated?.subscription_id;

  return (
    <main className="main">
      <button type="button" className="back-link" onClick={onBack}>← Back to recommendations</button>

      {error && (
        <div className="alert" role="alert">
          <span>{error}</span>
          <button type="button" className="btn btn--ghost btn--sm" onClick={() => { setError(null); refresh(); }}>
            Try again
          </button>
        </div>
      )}

      <div className="subs">
        <PendingDeliveries token={token} onChanged={loadSubs} onFail={fail} />

        {product && !draft && !error && <div className="sub-new sub-new--loading skeleton" aria-busy="true" />}

        {product && draft && (
          justCreated ? (
            <section className="sub-new sub-new--done" role="status">
              <h1>You're subscribed</h1>
              <p>
                {plural(justCreated.quantity, "pack")} of {justCreated.product.product_name} every{" "}
                {plural(justCreated.frequency_days, "day")}. First delivery{" "}
                <strong>{formatDate(justCreated.next_delivery_date)}</strong>.
              </p>
              <p className="muted">
                We'll message you the day before each delivery. Nothing is charged until you approve it in chat.
              </p>
              <div className="sub-new__actions">
                <button type="button" className="btn btn--primary" onClick={onBack}>Back to recommendations</button>
              </div>
            </section>
          ) : draft.existing_subscription ? (
            <section className="sub-new sub-new--done" role="status">
              <h1>You already subscribe to this</h1>
              <p>Manage your {draft.product.product_name} subscription below.</p>
            </section>
          ) : (
            <NewSubscription key={productId} rec={product} draft={draft} token={token}
                             onCreated={onCreated} onFail={fail} onRecheck={loadDraft} />
          )
        )}

        {!product && (
          <section className="sub-new sub-new--done">
            <h1>My subscriptions</h1>
            <p className="muted">
              Edit, skip, pause or cancel any subscription here. To add one, pick a product from your recommendations.
            </p>
          </section>
        )}

        <section className="sub-list" aria-labelledby="sub-list-title">
          <h2 id="sub-list-title" className={product ? undefined : "visually-hidden"}>Your subscriptions</h2>
          {!subs && !error && <p className="muted">Loading…</p>}
          {subs && subs.length === 0 && <p className="muted">No subscriptions yet.</p>}
          {subs && subs.length > 0 && (
            <div className="sub-list__items">
              {subs.map((s) => (
                <SubscriptionCard key={s.subscription_id} sub={s} token={token} highlight={s.subscription_id === existingId}
                                  onChanged={refresh} onFail={fail} />
              ))}
            </div>
          )}
        </section>
      </div>
    </main>
  );
}

// --------------------------------------------------------------------------- new subscription
function NewSubscription({ rec, draft, token, onCreated, onFail, onRecheck }) {
  const { limits, product: offer } = draft;
  // The draft carries the engine's recommendation (server side); the card's values are the fallback.
  const fromEngine = draft.recommendation?.source === "recommendation";
  const recQty = fromEngine ? draft.recommendation.quantity : (rec.suggested_quantity ?? null);
  const recEvery = fromEngine ? draft.recommendation.frequency_days : recommendedEvery(rec);
  const [quantity, setQuantity] = useState(() => String(clamp(recQty ?? draft.quantity, limits.quantity)));
  const [frequency, setFrequency] = useState(() => String(clamp(recEvery ?? draft.frequency_days, limits.frequency_days)));
  const [firstDate, setFirstDate] = useState(draft.first_delivery_date);
  const [slot, setSlot] = useState("");
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState(null);

  const q = toInt(quantity);
  const f = toInt(frequency);
  const valid = inRange(q, limits.quantity) && inRange(f, limits.frequency_days)
    && firstDate >= limits.earliest_delivery_date;
  const unit = Number(offer.subscriber_price);
  const regular = Number(offer.regular_price);

  const submit = (event) => {
    event.preventDefault();
    if (!valid || !draft.messaging_ready) return;
    setBusy(true);
    setFormError(null);
    createSubscription(token, {
      product_id: offer.product_id, quantity: q, frequency_days: f,
      first_delivery_date: firstDate, preferred_slot: slot || null,
    })
      .then((r) => onCreated(r.subscription))
      .catch((e) => {
        if (e.status === 401) onFail(e);
        else if (e.code === "messaging_required") onRecheck();
        else setFormError(e.message);
      })
      .finally(() => setBusy(false));
  };

  return (
    <section className="sub-new" aria-labelledby="sub-new-title">
      <div className="sub-new__product">
        <ProductImage src={rec.product_image ?? offer.image_url} alt={offer.product_name} category={offer.category} />
        <div>
          <p className="card__brand">{offer.brand}</p>
          <h1 id="sub-new-title">{offer.product_name}</h1>
          <p className="card__pack">{offer.pack_size}</p>
          <div className="price">
            <span className="price__now">{formatPrice(unit)}</span>
            {offer.has_discount && <span className="price__was">{formatPrice(regular)}</span>}
            {offer.has_discount && <span className="price__off">{Math.round(offer.saving_percent)}% off</span>}
          </div>
          <p className="price__note">
            {offer.has_discount ? "Subscriber price per pack" : "Price per pack"}
            {!offer.in_stock && <span className="sub-warn"> · Currently out of stock</span>}
          </p>
        </div>
      </div>

      {!draft.messaging_ready && (
        <div className="chat-connect" role="status">
          <strong>Connect chat notifications to subscribe</strong>
          <span>
            You approve every delivery in chat before anything is charged. Open our bot, tap <b>Start</b>, then
            share the phone number on your account.
          </span>
          <div className="chat-connect__actions">
            <a className="btn btn--primary btn--sm" href={CHAT_BOT_URL} target="_blank" rel="noreferrer">
              Open the bot on Telegram
            </a>
            <button type="button" className="link-btn" onClick={onRecheck}>I've connected, check again</button>
          </div>
        </div>
      )}

      <form className="sub-form" onSubmit={submit} noValidate>
        <label className="field">
          <span className="field__label">Quantity (packs per delivery)</span>
          <input className="sub-input" type="number" inputMode="numeric" min={limits.quantity[0]} max={limits.quantity[1]}
                 value={quantity} onChange={(e) => setQuantity(e.target.value)} aria-invalid={!inRange(q, limits.quantity)} />
          <span className="field__hint">
            {recQty ? `Recommended: ${plural(recQty, "pack")}` : `Between ${limits.quantity[0]} and ${limits.quantity[1]}`}
          </span>
        </label>

        <label className="field">
          <span className="field__label">Deliver every (days)</span>
          <input className="sub-input" type="number" inputMode="numeric" min={limits.frequency_days[0]}
                 max={limits.frequency_days[1]} value={frequency} onChange={(e) => setFrequency(e.target.value)}
                 aria-invalid={!inRange(f, limits.frequency_days)} />
          <span className="field__hint">
            {recEvery ? `You usually buy every ~${plural(recEvery, "day")}`
              : `Between ${limits.frequency_days[0]} and ${limits.frequency_days[1]} days`}
          </span>
        </label>

        <label className="field">
          <span className="field__label">First delivery</span>
          <input className="sub-input" type="date" min={limits.earliest_delivery_date} value={firstDate}
                 onChange={(e) => setFirstDate(e.target.value)} />
          <span className="field__hint">Defaults to your usual shopping day</span>
        </label>

        <label className="field">
          <span className="field__label">Preferred slot</span>
          <select className="sub-input" value={slot} onChange={(e) => setSlot(e.target.value)}>
            <option value="">Choose when approving</option>
            {limits.delivery_slots.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
          <span className="field__hint">You can still pick a slot each time</span>
        </label>

        <dl className="summary sub-form__summary">
          <div><dt>Per delivery</dt><dd>{valid ? formatPrice(unit * q) : "-"}</dd></div>
          {offer.has_discount && valid && (
            <div><dt>You save per delivery</dt><dd className="accent">{formatPrice((regular - unit) * q)}</dd></div>
          )}
          <div><dt>Schedule</dt><dd>{valid ? `every ${plural(f, "day")} from ${formatDate(firstDate)}` : "-"}</dd></div>
        </dl>

        <p className="sub-form__note">
          The day before each delivery we'll message you to approve it, skip it or delay it. You're never charged
          without approving.
        </p>

        {formError && <p className="form-error" role="alert">{formError}</p>}

        <div className="sub-new__actions">
          <button type="submit" className="btn btn--primary" disabled={!valid || busy || !draft.messaging_ready}>
            {busy ? "Subscribing…" : "Confirm subscription"}
          </button>
        </div>
      </form>
    </section>
  );
}

// --------------------------------------------------------------------------- existing subscriptions
const STATUS_LABEL = { ACTIVE: "Active", PAUSED: "Paused", CANCELLED: "Cancelled" };

/** "25 Nov 2026" in IST from a UTC timestamp such as cancelled_at. */
function formatLocalDay(isoUtc) {
  return new Date(isoUtc).toLocaleDateString("en-IN",
    { day: "numeric", month: "short", year: "numeric", timeZone: "Asia/Kolkata" });
}

function SubscriptionCard({ sub, token, highlight, onChanged, onFail }) {
  const [editing, setEditing] = useState(false);
  const [quantity, setQuantity] = useState(String(sub.quantity));
  const [frequency, setFrequency] = useState(String(sub.frequency_days));
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState(null);
  const [cardError, setCardError] = useState(null);

  const run = (promise, done) => {
    setBusy(true);
    setCardError(null);
    setMessage(null);
    promise
      .then((r) => {
        setEditing(false);
        if (done) setMessage(done(r));
        onChanged();
      })
      .catch((e) => (e.status === 401 ? onFail(e) : setCardError(e.message)))
      .finally(() => setBusy(false));
  };

  const save = (event) => {
    event.preventDefault();
    run(updateSubscription(token, sub.subscription_id, { quantity: toInt(quantity), frequency_days: toInt(frequency) }),
      () => "Saved");
  };
  const skip = () => {
    if (window.confirm(`Skip your ${formatDate(sub.next_delivery_date)} delivery?`)) {
      run(skipNextDelivery(token, sub.subscription_id), (r) => `Skipped ${formatDate(r.skipped_date)}`);
    }
  };
  const cancel = () => {
    if (window.confirm(`Cancel your ${sub.product.product_name} subscription? Future deliveries stop.`)) {
      run(cancelSubscription(token, sub.subscription_id));
    }
  };
  const setStatus = (status) => run(updateSubscription(token, sub.subscription_id, { status }));

  const p = sub.product;
  const cancelled = sub.status === "CANCELLED";
  return (
    <article className={`sub-card${highlight ? " sub-card--highlight" : ""}${sub.status === "PAUSED" ? " sub-card--paused" : ""}${cancelled ? " sub-card--cancelled" : ""}`}>
      <div className="sub-card__media">
        <ProductImage src={p.image_url} alt={p.product_name} category={p.category} />
      </div>
      <div className="sub-card__body">
        <div className="sub-card__top">
          <h3 className="sub-card__title">{p.product_name}</h3>
          <span className={`sub-status sub-status--${sub.status.toLowerCase()}`}>{STATUS_LABEL[sub.status]}</span>
        </div>
        <p className="card__pack">{p.pack_size}</p>

        {!editing ? (
          <ul className="facts">
            <li><span className="facts__label">Quantity</span><span>{plural(sub.quantity, "pack")}</span></li>
            <li><span className="facts__label">Every</span><span>{plural(sub.frequency_days, "day")}</span></li>
            <li>
              <span className="facts__label">
                {cancelled ? "Cancelled" : sub.status === "PAUSED" ? "Paused" : "Next delivery"}
              </span>
              <span>
                {cancelled
                  ? (sub.cancelled_at ? `on ${formatLocalDay(sub.cancelled_at)}` : "no more deliveries")
                  : sub.status === "PAUSED"
                    ? (sub.paused_until ? `until ${formatDate(sub.paused_until)}` : "until you resume")
                    : formatDate(sub.next_delivery_date)}
              </span>
            </li>
            <li><span className="facts__label">Per delivery</span><span>{formatPrice(Number(sub.total_per_delivery))}</span></li>
            {Number(sub.saving_per_delivery) > 0 && (
              <li><span className="facts__label">You save</span>
                <span className="accent">{formatPrice(Number(sub.saving_per_delivery))}</span></li>
            )}
          </ul>
        ) : (
          <form className="sub-card__edit" onSubmit={save}>
            <label className="field">
              <span className="field__label">Quantity</span>
              <input className="sub-input" type="number" min="1" value={quantity} onChange={(e) => setQuantity(e.target.value)} />
            </label>
            <label className="field">
              <span className="field__label">Every (days)</span>
              <input className="sub-input" type="number" min="1" value={frequency} onChange={(e) => setFrequency(e.target.value)} />
            </label>
            <div className="sub-card__actions">
              <button type="submit" className="btn btn--primary btn--sm" disabled={busy}>Save</button>
              <button type="button" className="btn btn--ghost btn--sm" onClick={() => setEditing(false)}>Cancel</button>
            </div>
          </form>
        )}

        {sub.price_changed && !cancelled && (
          <p className="sub-warn">
            Price changed to {formatPrice(Number(sub.current_subscriber_price))} per pack. You'll confirm it when
            approving your next delivery.
          </p>
        )}
        {message && <p className="sub-ok" role="status">{message}</p>}
        {cardError && <p className="form-error" role="alert">{cardError}</p>}

        {cancelled && <p className="muted">No more deliveries. Past orders stay in your order history.</p>}

        {!editing && !cancelled && (
          <div className="sub-card__actions">
            <button type="button" className="btn btn--ghost btn--sm" disabled={busy} onClick={() => setEditing(true)}>Edit</button>
            {sub.status === "ACTIVE" && (
              <>
                <button type="button" className="btn btn--ghost btn--sm" disabled={busy} onClick={skip}>Skip next</button>
                <button type="button" className="btn btn--ghost btn--sm" disabled={busy} onClick={() => setStatus("PAUSED")}>Pause</button>
              </>
            )}
            {sub.status === "PAUSED" && (
              <button type="button" className="btn btn--ghost btn--sm" disabled={busy} onClick={() => setStatus("ACTIVE")}>Resume</button>
            )}
            <button type="button" className="btn btn--ghost btn--sm sub-card__cancel" disabled={busy} onClick={cancel}>
              Cancel subscription
            </button>
          </div>
        )}
      </div>
    </article>
  );
}
