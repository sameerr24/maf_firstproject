import { useCallback, useEffect, useState } from "react";
import Header from "./components/Header.jsx";
import LoginPage from "./pages/LoginPage.jsx";
import OrderHistoryPage from "./pages/OrderHistoryPage.jsx";
import RecommendationsPage from "./pages/RecommendationsPage.jsx";
import SubscriptionPage from "./pages/SubscriptionPage.jsx";
import { logoutCustomer } from "./api/recommendations.js";
import { refreshAccessToken, setApiAccessToken } from "./api/client.js";
import { clearSession, loadSelectedProduct, saveSelectedProduct } from "./session.js";

// Hash routes, so the browser's back button works without a router library:
//   #/                       recommendations
//   #/subscribe/<product_id> subscription page for one recommended product
//   #/subscriptions          the customer's subscriptions (header link)
//   #/orders                 order history (header link)
function parseRoute() {
  if (window.location.hash === "#/subscriptions") return { page: "subscriptions" };
  if (window.location.hash === "#/orders") return { page: "orders" };
  const match = window.location.hash.match(/^#\/subscribe\/(.+)$/);
  return match ? { page: "subscribe", productId: decodeURIComponent(match[1]) } : { page: "recommendations" };
}

export default function App() {
  const [session, setSession] = useState(null);
  const [sessionReady, setSessionReady] = useState(false);
  const [route, setRoute] = useState(parseRoute);
  const [logoutError, setLogoutError] = useState(null);
  const [restoreError, setRestoreError] = useState(null);

  useEffect(() => {
    const onSessionRefreshed = (event) => {
      setApiAccessToken(event.detail.token);
      setSession(event.detail);
    };
    window.addEventListener("auth:session-refreshed", onSessionRefreshed);
    return () => window.removeEventListener("auth:session-refreshed", onSessionRefreshed);
  }, []);

  useEffect(() => {
    let active = true;
    refreshAccessToken()
      .catch((error) => {
        if (active && error.status !== 401) {
          setRestoreError("Couldn't restore your session. Check your connection and try again.");
        }
      })
      .finally(() => {
        if (active) setSessionReady(true);
      });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    const onHashChange = () => setRoute(parseRoute());
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);

  const handleLogin = useCallback((next) => {
    setApiAccessToken(next.token);
    setSession(next);
    setLogoutError(null);
    setRestoreError(null);
    window.location.hash = "#/";
  }, []);

  const clearExpiredSession = useCallback(() => {
    setApiAccessToken(null);
    clearSession();
    setSession(null);
    window.location.hash = "#/";
  }, []);

  const handleLogout = useCallback(async () => {
    setLogoutError(null);
    try {
      await logoutCustomer(session?.token);
      clearExpiredSession();
    } catch (error) {
      setLogoutError(`Couldn't securely sign out. ${error.message}`);
    }
  }, [clearExpiredSession, session?.token]);

  const openSubscription = useCallback((rec) => {
    saveSelectedProduct(rec);
    window.location.hash = `#/subscribe/${encodeURIComponent(rec.product_id)}`;
  }, []);

  if (!sessionReady) return <main className="main"><p className="muted">Restoring your secure session…</p></main>;
  if (!session?.token) return <LoginPage onLogin={handleLogin} initialError={restoreError} />;

  return (
    <div className="page">
      <Header customerId={session.customerId} onSwitchCustomer={handleLogout} currentPage={route.page} />
      {logoutError && <p className="form-error logout-error" role="alert">{logoutError}</p>}
      {route.page === "orders" ? (
        <OrderHistoryPage session={session} onSessionExpired={clearExpiredSession}
                          onBack={() => { window.location.hash = "#/"; }} />
      ) : route.page === "subscribe" || route.page === "subscriptions" ? (
        <SubscriptionPage product={route.page === "subscribe" ? loadSelectedProduct(route.productId) : null}
                          session={session} customerId={session.customerId}
                          onSessionExpired={clearExpiredSession}
                          onBack={() => { window.location.hash = "#/"; }} />
      ) : (
        <RecommendationsPage session={session} onSubscribe={openSubscription}
                             onSessionExpired={clearExpiredSession} />
      )}
    </div>
  );
}
