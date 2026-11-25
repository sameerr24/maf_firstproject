// Order history endpoints of the Flask API (read-only).
import { flaskRequest } from "./flask.js";

/** One page of the customer's orders, newest first. source: "manual" | "subscription" | undefined (all). */
export function getOrders(token, { limit = 20, offset = 0, source } = {}) {
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  if (source) params.set("source", source);
  return flaskRequest(`/api/orders?${params}`, { token });
}
