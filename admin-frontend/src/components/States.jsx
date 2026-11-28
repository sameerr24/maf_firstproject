export function Loading({ label = "Loading…" }) {
  return <p className="state" role="status">{label}</p>;
}

export function ErrorBox({ error, onRetry }) {
  return (
    <div className="state state--error" role="alert">
      <p>{error.message}</p>
      {onRetry && <button type="button" className="btn btn--ghost" onClick={onRetry}>Try again</button>}
    </div>
  );
}

export function Empty({ title, children }) {
  return (
    <div className="state state--empty">
      <p className="state__title">{title}</p>
      {children && <p className="muted">{children}</p>}
    </div>
  );
}

export function Notice({ notice, onClose }) {
  if (!notice) return null;
  return (
    <div className={`notice notice--${notice.tone ?? "ok"}`} role="status">
      <span>{notice.text}</span>
      <button type="button" className="icon-btn" aria-label="Dismiss" onClick={onClose}>×</button>
    </div>
  );
}

const STATUS_LABELS = { short: "Short", low: "Low", ok: "OK" };

export function StockChip({ status }) {
  return <span className={`chip chip--${status}`}>{STATUS_LABELS[status] ?? status}</span>;
}
