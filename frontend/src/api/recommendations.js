import { apiRequest } from "./client.js";

export function getWidgetConfig() {
  return apiRequest("/api/v1/auth/widget-config");
}

export function phoneLogin(body) {
  return apiRequest("/api/v1/auth/login", { method: "POST", body });
}

export function registerCustomer(body) {
  return apiRequest("/api/v1/auth/register", { method: "POST", body });
}

export function logoutCustomer(token) {
  return apiRequest("/api/v1/auth/logout", { method: "POST", token });
}

/** Ranked recommendations for the logged-in customer. */
export function fetchMyRecommendations(token, limit = 10) {
  return apiRequest(`/api/v1/recommendations/me?limit=${limit}`, { token });
}
