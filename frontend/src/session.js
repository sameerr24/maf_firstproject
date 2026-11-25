// Session state lives in sessionStorage: it survives a page reload but not a closed tab.

const SESSION_KEY = "smart-reorder.session";
const SELECTED_KEY = "smart-reorder.selected-product";

function read(key) {
  try {
    return JSON.parse(sessionStorage.getItem(key));
  } catch {
    return null;
  }
}

export function loadSession() {
  const s = read(SESSION_KEY);
  return s?.token && s?.customerId ? s : null;
}

export function saveSession(session) {
  sessionStorage.setItem(SESSION_KEY, JSON.stringify(session));
}

export function clearSession() {
  sessionStorage.removeItem(SESSION_KEY);
  sessionStorage.removeItem(SELECTED_KEY);
}

/** The recommendation the customer chose to subscribe to (read by the subscription page). */
export function saveSelectedProduct(rec) {
  sessionStorage.setItem(SELECTED_KEY, JSON.stringify(rec));
}

export function loadSelectedProduct(productId) {
  const rec = read(SELECTED_KEY);
  return rec?.product_id === productId ? rec : null;
}
