// Subscriptions endpoints of the Flask API.
import { flaskRequest as request } from "./flask.js";

/** Pre-filled values for a new subscription, plus the product's prices and limits. */
export const getDraft = (token, productId) =>
  request(`/api/subscriptions/draft?product_id=${encodeURIComponent(productId)}`, { token });

/** All of the customer's subscriptions, cancelled ones included (shown greyed out, last). */
export const listSubscriptions = (token) => request("/api/subscriptions?include_cancelled=1", { token });

export const createSubscription = (token, body) =>
  request("/api/subscriptions", { token, method: "POST", body });

export const updateSubscription = (token, id, body) =>
  request(`/api/subscriptions/${id}`, { token, method: "PATCH", body });

export const cancelSubscription = (token, id) =>
  request(`/api/subscriptions/${id}`, { token, method: "DELETE" });

export const skipNextDelivery = (token, id) =>
  request(`/api/subscriptions/${id}/skip`, { token, method: "POST" });

// Replies to delivery reminders: the website's version of the chat buttons.
/** Deliveries waiting for the customer's reply (a reminder was sent in chat). */
export const getPendingDeliveries = (token) => request("/api/subscriptions/deliveries/pending", { token });

export const approveDelivery = (token, occurrenceId, slot) =>
  request(`/api/subscriptions/deliveries/${occurrenceId}/approve`, { token, method: "POST", body: { slot } });

export const skipDelivery = (token, occurrenceId) =>
  request(`/api/subscriptions/deliveries/${occurrenceId}/skip`, { token, method: "POST" });

export const delayDelivery = (token, occurrenceId, days) =>
  request(`/api/subscriptions/deliveries/${occurrenceId}/delay`, { token, method: "POST", body: { days } });

// The admin asked the customer to confirm cancelling (operational reasons). Same answer as the
// chat's "Yes, cancel it" / "Keep my subscription" buttons; the first answer on either channel wins.
export const confirmCancelRequest = (token, id) =>
  request(`/api/subscriptions/${id}/cancel-request/confirm`, { token, method: "POST" });

export const declineCancelRequest = (token, id) =>
  request(`/api/subscriptions/${id}/cancel-request/decline`, { token, method: "POST" });
