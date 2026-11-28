// Hash routes: #/at-risk (default), #/inventory, #/subscriptions?product_id=…&customer_id=…

export const PAGES = ["at-risk", "inventory", "subscriptions"];

export function parseHash(hash) {
  const [path, query = ""] = (hash || "").replace(/^#\/?/, "").split("?");
  const page = PAGES.includes(path) ? path : "at-risk";
  return { page, params: Object.fromEntries(new URLSearchParams(query)) };
}

export function toHash(page, params = {}) {
  const query = new URLSearchParams(Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== ""));
  const qs = query.toString();
  return `#/${page}${qs ? `?${qs}` : ""}`;
}
