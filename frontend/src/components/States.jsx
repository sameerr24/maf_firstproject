export function SkeletonGrid({ count = 10 }) {
  return (
    <div className="grid" aria-busy="true" aria-label="Loading recommendations">
      {Array.from({ length: count }, (_, i) => (
        <div className="card card--skeleton" key={i}>
          <div className="skeleton skeleton--media" />
          <div className="card__body">
            <div className="skeleton skeleton--line short" />
            <div className="skeleton skeleton--line" />
            <div className="skeleton skeleton--line" />
            <div className="skeleton skeleton--line short" />
          </div>
        </div>
      ))}
    </div>
  );
}

export function EmptyState({ title, children, action }) {
  return (
    <div className="empty">
      <div className="empty__art" aria-hidden="true">
        <svg viewBox="0 0 64 64" width="56" height="56">
          <path d="M10 18h6l6 26h26l6-18H20" fill="none" stroke="currentColor" strokeWidth="3"
                strokeLinecap="round" strokeLinejoin="round" />
          <circle cx="26" cy="52" r="3.5" fill="currentColor" />
          <circle cx="46" cy="52" r="3.5" fill="currentColor" />
        </svg>
      </div>
      <h2>{title}</h2>
      <p>{children}</p>
      {action}
    </div>
  );
}

