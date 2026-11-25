import Brand from "./Brand.jsx";

export default function Header({ customerId, onSwitchCustomer, currentPage }) {
  return (
    <header className="header">
      <div className="header__inner">
        <a href="#/" className="header__home" aria-label="Back to recommendations"><Brand /></a>
        <div className="header__account">
          <a href="#/subscriptions" className="header__link"
             aria-current={currentPage === "subscriptions" ? "page" : undefined}>My subscriptions</a>
          <a href="#/orders" className="header__link"
             aria-current={currentPage === "orders" ? "page" : undefined}>Orders</a>
          <span className="avatar" aria-hidden="true">
            <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="2"
                 strokeLinecap="round"><circle cx="12" cy="8" r="4" /><path d="M4 21c1.5-4 4.5-6 8-6s6.5 2 8 6" /></svg>
          </span>
          <span className="header__who">
            <small>Signed in as</small>
            <strong>{customerId}</strong>
          </span>
          <button type="button" className="btn btn--ghost btn--sm" onClick={onSwitchCustomer}>Switch customer</button>
        </div>
      </div>
    </header>
  );
}
