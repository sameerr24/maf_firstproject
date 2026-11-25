// Calls to the Flask subscriptions API (a separate service from the FastAPI recommendation API).
// Same token, same error shape: {"error": {"code", "message", ...}}.
import { ApiError } from "./client.js";

export const SUBSCRIPTIONS_API = (import.meta.env.VITE_SUBSCRIPTIONS_API ?? "http://localhost:5001").replace(/\/$/, "");

async function request(path, { token, method = "GET", body } = {}) {
  const headers = { Authorization: `Bearer ${token}` };
  if (body !== undefined) headers["Content-Type"] = "application/json";

  let response;
  try {
    response = await fetch(`${SUBSCRIPTIONS_API}${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, "network_error",
      `Can't reach the subscriptions service at ${SUBSCRIPTIONS_API}. Is it running?`);
  }

  const data = await response.json().catch(() => null);
  if (!response.ok) {
    const err = data?.error ?? {};
    const apiError = new ApiError(response.status, err.code ?? "http_error",
      err.message ?? `Request failed (${response.status})`);
    apiError.details = err;
    throw apiError;
  }
  return data;
}

/** Pre-filled values for a new subscription, plus the product's prices and limits. */
export const getDraft = (token, productId) =>
  request(`/api/subscriptions/draft?product_id=${encodeURIComponent(productId)}`, { token });

export const listSubscriptions = (token) => request("/api/subscriptions", { token });

export const createSubscription = (token, body) =>
  request("/api/subscriptions", { token, method: "POST", body });

export const updateSubscription = (token, id, body) =>
  request(`/api/subscriptions/${id}`, { token, method: "PATCH", body });

export const cancelSubscription = (token, id) =>
  request(`/api/subscriptions/${id}`, { token, method: "DELETE" });

export const skipNextDelivery = (token, id) =>
  request(`/api/subscriptions/${id}/skip`, { token, method: "POST" });
