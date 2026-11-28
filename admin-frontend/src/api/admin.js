// Admin API client (Flask on 127.0.0.1:5002). No auth: the admin app is local-only by design.
import { buildUrl } from "./url.js";

const BASE = import.meta.env.VITE_ADMIN_API_URL;

export class ApiError extends Error {
  constructor(status, code, message) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

async function request(path, { method = "GET", body, query } = {}) {
  const write = method !== "GET";
  let response;
  try {
    response = await fetch(buildUrl(BASE, path, query), {
      method,
      headers: write ? { Accept: "application/json", "Content-Type": "application/json" } : { Accept: "application/json" },
      body: write ? JSON.stringify(body ?? {}) : undefined,       // writes are always JSON (the API requires it)
    });
  } catch {
    throw new ApiError(0, "network", "Can't reach the admin API. Is `python run_admin.py` running on port 5002?");
  }
  let data = null;
  try { data = await response.json(); } catch { /* empty or non-JSON body */ }
  if (!response.ok) {
    throw new ApiError(response.status, data?.error?.code ?? "error",
      data?.error?.message ?? `Request failed (${response.status})`);
  }
  return data;
}

export const getAtRisk = () => request("/at-risk");
export const getInventory = (query) => request("/inventory", { query });
export const getSubscriptions = (query) => request("/subscriptions", { query });
export const getMovements = (productId, limit = 50) =>
  request(`/inventory/${encodeURIComponent(productId)}/movements`, { query: { limit } });
export const restock = (productId, quantity, note) =>
  request(`/inventory/${encodeURIComponent(productId)}/restock`, { method: "POST", body: { quantity, note: note || undefined } });
export const adjust = (productId, availableQty, reason) =>
  request(`/inventory/${encodeURIComponent(productId)}/adjust`, { method: "POST", body: { available_qty: availableQty, reason } });
export const delayDelivery = (subscriptionId, days) =>
  request(`/subscriptions/${encodeURIComponent(subscriptionId)}/delay`, { method: "POST", body: { days } });
export const skipDelivery = (subscriptionId) =>
  request(`/subscriptions/${encodeURIComponent(subscriptionId)}/skip`, { method: "POST", body: {} });
export const requestCancellation = (subscriptionId) =>
  request(`/subscriptions/${encodeURIComponent(subscriptionId)}/cancel-request`, { method: "POST", body: {} });
export const withdrawCancellation = (subscriptionId) =>
  request(`/subscriptions/${encodeURIComponent(subscriptionId)}/cancel-request/withdraw`, { method: "POST", body: {} });
