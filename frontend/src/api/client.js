// Every API error has the shape {"error": {"code", "message", "request_id"}}.

export const API_BASE = (import.meta.env.VITE_RECOMMENDATION_API ?? "http://localhost:8000").replace(/\/$/, "");

export class ApiError extends Error {
  constructor(status, code, message, requestId) {
    super(message);
    this.status = status;
    this.code = code;
    this.requestId = requestId;
  }
}

let currentAccessToken = null;
let refreshInProgress = null;

export function setApiAccessToken(token) {
  currentAccessToken = token;
}

export function getApiAccessToken(fallback) {
  return currentAccessToken ?? fallback ?? null;
}

async function responseData(response) {
  return response.json().catch(() => null);
}

function apiError(response, data) {
  const err = data?.error ?? {};
  return new ApiError(response.status, err.code ?? "http_error",
    err.message ?? `Request failed (${response.status})`, err.request_id);
}

export async function refreshAccessToken() {
  if (!refreshInProgress) {
    refreshInProgress = (async () => {
      let response;
      try {
        response = await fetch(`${API_BASE}/api/v1/auth/refresh`, {
          method: "POST",
          credentials: "include",
        });
      } catch {
        throw new ApiError(0, "network_error", "Couldn't refresh the session. Check your connection.");
      }
      const data = await responseData(response);
      if (!response.ok) throw apiError(response, data);
      if (typeof data?.access_token !== "string" || !data.access_token) {
        throw new ApiError(502, "invalid_response", "The authentication service returned an invalid response.");
      }
      currentAccessToken = data.access_token;
      window.dispatchEvent(new CustomEvent("auth:session-refreshed", {
        detail: {
          token: data.access_token,
          customerId: data.customer_id,
          customerName: data.customer_name,
        },
      }));
      return data.access_token;
    })().finally(() => {
      refreshInProgress = null;
    });
  }
  return refreshInProgress;
}

async function request(path, options, accessToken, canRefresh) {
  const headers = {};
  if (accessToken) headers.Authorization = `Bearer ${accessToken}`;
  if (options.body !== undefined) headers["Content-Type"] = "application/json";

  let response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      method: options.method,
      headers,
      credentials: "include",
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
    });
  } catch {
    throw new ApiError(0, "network_error",
      `Can't reach the recommendation service at ${API_BASE}. Is it running?`);
  }

  const data = await responseData(response);
  if (!response.ok) {
    const error = apiError(response, data);
    if (response.status === 401 && accessToken && canRefresh && !path.startsWith("/api/v1/auth/")) {
      try {
        const nextToken = await refreshAccessToken();
        return request(path, options, nextToken, false);
      } catch (refreshError) {
        if (refreshError instanceof ApiError && refreshError.status === 401) throw error;
        throw refreshError;
      }
    }
    throw error;
  }
  return data;
}

export function apiRequest(path, { token, method = "GET", body } = {}) {
  return request(path, { method, body }, currentAccessToken ?? token ?? null, true);
}
