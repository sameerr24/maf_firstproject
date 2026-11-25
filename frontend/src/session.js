// The selected recommendation survives a reload; access tokens remain in memory.

const SELECTED_KEY = "smart-reorder.selected-product";

function read(key) {
  try {
    return JSON.parse(sessionStorage.getItem(key));
  } catch {
    return null;
  }
}

export function clearSession() {
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
