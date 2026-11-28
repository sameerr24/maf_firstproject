// Pure helpers (no Vite, no DOM) so they can be unit tested with node --test.

export const DEFAULT_API_URL = "http://127.0.0.1:5002";

/** Full URL for an admin API path; empty / null query values are left out. */
export function buildUrl(base, path, query = {}) {
  const url = new URL(`${(base || DEFAULT_API_URL).replace(/\/+$/, "")}/api/admin${path}`);
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined && value !== null && value !== "") url.searchParams.set(key, String(value));
  }
  return url.toString();
}
