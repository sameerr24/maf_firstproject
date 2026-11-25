import { useEffect, useState } from "react";
import { getPendingDeliveries } from "../api/subscriptions.js";
import { getApiAccessToken } from "../api/client.js";

// Count of delivery reminders waiting for a reply, shown on the header's "My subscriptions"
// link (subscriptions module). Polls on its own; the panel on the subscriptions page also
// broadcasts its count (PENDING_EVENT) so both update together after a reply.

const POLL_MS = 30000;
export const PENDING_EVENT = "pending-deliveries-changed";

export default function PendingBadge() {
  const [count, setCount] = useState(0);

  useEffect(() => {
    const load = () => {
      const token = getApiAccessToken();
      if (!token) return;
      getPendingDeliveries(token).then((d) => setCount(d.deliveries.length)).catch(() => {});  // badge is best effort
    };
    const onCount = (e) => setCount(e.detail);
    const onVisible = () => document.visibilityState === "visible" && load();
    load();
    const timer = setInterval(load, POLL_MS);
    window.addEventListener(PENDING_EVENT, onCount);
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      clearInterval(timer);
      window.removeEventListener(PENDING_EVENT, onCount);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, []);

  if (count === 0) return null;
  return (
    <span className="header__badge" title={`${count} delivery ${count === 1 ? "reminder" : "reminders"} waiting for your reply`}>
      {count}<span className="visually-hidden"> waiting for your reply</span>
    </span>
  );
}
