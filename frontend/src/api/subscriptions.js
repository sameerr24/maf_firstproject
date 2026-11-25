// Subscriptions endpoints of the Flask API.
import { flaskRequest as request } from "./flask.js";

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
