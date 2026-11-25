export default function Brand() {
  return (
    <span className="brand">
      <span className="brand__mark" aria-hidden="true">
        <svg viewBox="0 0 24 24" width="18" height="18">
          <path d="M5 12.5l4.5 4.5L19 7.5" fill="none" stroke="currentColor" strokeWidth="2.6"
                strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </span>
      <span className="brand__name">Smart<span>Reorder</span></span>
    </span>
  );
}
