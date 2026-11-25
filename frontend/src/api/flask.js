// Request helper for the Flask API (subscriptions, orders), a separate service from the
// FastAPI recommendation API. Same token, same error shape: {"error": {"code", "message", ...}}.
import { ApiError } from "./client.js";

export const FLASK_API = (import.meta.env.VITE_SUBSCRIPTIONS_API ?? "http://localhost:5001").replace(/\/$/, "");

export async function flaskRequest(path, { token, method = "GET", body } = {}) {
  const headers = { Authorization: `Bearer ${token}` };
  if (body !== undefined) headers["Content-Type"] = "application/json";

  let response;
  try {
    response = await fetch(`${FLASK_API}${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, "network_error", `Can't reach the subscriptions service at ${FLASK_API}. Is it running?`);
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
