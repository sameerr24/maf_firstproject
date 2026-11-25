// Request helper for the Flask API (subscriptions, orders), a separate service from FastAPI.
import { ApiError, getApiAccessToken, refreshAccessToken } from "./client.js";

export const FLASK_API = (import.meta.env.VITE_SUBSCRIPTIONS_API ?? "http://localhost:5001").replace(/\/$/, "");

async function request(path, { method, body }, token, canRefresh) {
  const headers = {};
  if (token) headers.Authorization = `Bearer ${token}`;
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
    if (response.status === 401 && token && canRefresh) {
      try {
        const nextToken = await refreshAccessToken();
        return request(path, { method, body }, nextToken, false);
      } catch (refreshError) {
        if (refreshError instanceof ApiError && refreshError.status === 401) throw apiError;
        throw refreshError;
      }
    }
    throw apiError;
  }
  return data;
}

export function flaskRequest(path, { token, method = "GET", body } = {}) {
  return request(path, { method, body }, getApiAccessToken(token), true);
}
