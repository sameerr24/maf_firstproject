import { apiRequest } from "./client.js";

/** Exchange a customer ID (exactly as stored in the customers table) for a bearer token.
 *  Demo login: no password; the API must run with AUTH_DEMO_LOGIN=true. */
export function demoLogin(customerId) {
  return apiRequest("/api/v1/auth/demo-login", { method: "POST", body: { customer_id: customerId } });
}

/** Ranked recommendations for the logged-in customer. */
export function fetchMyRecommendations(token, limit = 10) {
  return apiRequest(`/api/v1/recommendations/me?limit=${limit}`, { token });
}
