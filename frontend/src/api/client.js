// Minimal fetch wrapper for the FastAPI recommendation service.
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

export async function apiRequest(path, { token, method = "GET", body } = {}) {
  const headers = {};
  if (token) headers.Authorization = `Bearer ${token}`;
  if (body !== undefined) headers["Content-Type"] = "application/json";

  let response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, "network_error",
      `Can't reach the recommendation service at ${API_BASE}. Is it running?`);
  }

  const data = await response.json().catch(() => null);
  if (!response.ok) {
    const err = data?.error ?? {};
    throw new ApiError(response.status, err.code ?? "http_error",
      err.message ?? `Request failed (${response.status})`, err.request_id);
  }
  return data;
}
