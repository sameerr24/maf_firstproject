/** Delay / Skip buttons for one delivery; `onAction(mode, target)` opens DeliveryActionModal. */
export default function DeliveryButtons({ target, onAction }) {
  const canDelay = (target.delay_options ?? []).length > 0;
  return (
    <div className="row-actions">
      <button type="button" className="btn btn--ghost btn--small" disabled={!canDelay}
              title={canDelay ? undefined : "Already delayed as far as allowed (2 days)"}
              onClick={() => onAction("delay", target)}>Delay</button>
      <button type="button" className="btn btn--ghost btn--small" onClick={() => onAction("skip", target)}>Skip</button>
    </div>
  );
}
