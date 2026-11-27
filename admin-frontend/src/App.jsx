import { useCallback, useEffect, useState } from "react";
import { getAtRisk } from "./api/admin.js";
import AtRiskPage from "./pages/AtRiskPage.jsx";
import InventoryPage from "./pages/InventoryPage.jsx";
import SubscriptionsPage from "./pages/SubscriptionsPage.jsx";
import { parseHash, toHash } from "./router.js";
import { usePolling } from "./usePolling.js";

const NAV = [["at-risk", "At risk"], ["inventory", "Inventory"], ["subscriptions", "Subscriptions"]];
const BADGE_POLL_MS = 15000;

export default function App() {
  const [route, setRoute] = useState(() => parseHash(window.location.hash));
  const [atRisk, setAtRisk] = useState(0);

  useEffect(() => {
    const onHash = () => { setRoute(parseHash(window.location.hash)); window.scrollTo(0, 0); };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const navigate = useCallback((page, params) => { window.location.hash = toHash(page, params); }, []);
  const refreshBadge = useCallback(() => getAtRisk().then((d) => setAtRisk(d.total)).catch(() => {}), []);
  usePolling(refreshBadge, BADGE_POLL_MS);

  return (
    <div className="app">
      <header className="topbar">
        <div className="topbar__brand">
          <span className="logo" aria-hidden="true" />
          <span>Inventory admin</span>
          <span className="local-tag" title="Runs on this computer only; no login by design.">local only</span>
        </div>
        <nav className="nav" aria-label="Admin">
          {NAV.map(([page, label]) => (
            <a key={page} href={toHash(page)} className={route.page === page ? "is-active" : ""}
               aria-current={route.page === page ? "page" : undefined}>
              {label}
              {page === "at-risk" && atRisk > 0 && (
                <span className="badge" aria-label={`${atRisk} at risk`}>{atRisk}</span>
              )}
            </a>
          ))}
        </nav>
      </header>
      <main className="main">
        {route.page === "at-risk" && <AtRiskPage onChanged={setAtRisk} />}
        {route.page === "inventory" && <InventoryPage onChanged={refreshBadge} />}
        {route.page === "subscriptions" && <SubscriptionsPage params={route.params} navigate={navigate} />}
      </main>
    </div>
  );
}
