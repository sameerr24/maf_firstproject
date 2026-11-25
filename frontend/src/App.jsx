import { useCallback, useEffect, useState } from "react";
import Header from "./components/Header.jsx";
import LoginPage from "./pages/LoginPage.jsx";
import RecommendationsPage from "./pages/RecommendationsPage.jsx";
import SubscriptionPage from "./pages/SubscriptionPage.jsx";
import { clearSession, loadSelectedProduct, loadSession, saveSelectedProduct, saveSession } from "./session.js";

// Hash routes, so the browser's back button works without a router library:
//   #/                       recommendations
//   #/subscribe/<product_id> subscription page for one recommended product
//   #/subscriptions          the customer's subscriptions (header link)
function parseRoute() {
  if (window.location.hash === "#/subscriptions") return { page: "subscriptions" };
  const match = window.location.hash.match(/^#\/subscribe\/(.+)$/);
  return match ? { page: "subscribe", productId: decodeURIComponent(match[1]) } : { page: "recommendations" };
}

export default function App() {
  const [session, setSession] = useState(loadSession);
  const [route, setRoute] = useState(parseRoute);

  useEffect(() => {
    const onHashChange = () => setRoute(parseRoute());
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);

  const handleLogin = useCallback((next) => {
    saveSession(next);
    setSession(next);
    window.location.hash = "#/";
  }, []);

  const handleLogout = useCallback(() => {
    clearSession();
    setSession(null);
    window.location.hash = "#/";
  }, []);

  const openSubscription = useCallback((rec) => {
    saveSelectedProduct(rec);
    window.location.hash = `#/subscribe/${encodeURIComponent(rec.product_id)}`;
  }, []);

  if (!session) return <LoginPage onLogin={handleLogin} />;

  return (
    <div className="page">
      <Header customerId={session.customerId} onSwitchCustomer={handleLogout} currentPage={route.page} />
      {route.page === "subscribe" || route.page === "subscriptions" ? (
        <SubscriptionPage product={route.page === "subscribe" ? loadSelectedProduct(route.productId) : null}
                          customerId={session.customerId} onBack={() => { window.location.hash = "#/"; }} />
      ) : (
        <RecommendationsPage session={session} onSubscribe={openSubscription} onSessionExpired={handleLogout} />
      )}
    </div>
  );
}
