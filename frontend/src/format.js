// Display helpers. All wording derives from the API's numbers; nothing is product-specific.

const inr = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 2 });

export const formatPrice = (value) => (value == null ? "" : inr.format(value));

export function formatDate(iso) {
  if (!iso) return "";
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d).toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" });
}

export const plural = (n, one, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

// Friendly label + tone for each replenishment_status coming from the engine.
export const STATUS = {
  "Overdue": { label: "Overdue", tone: "danger" },
  "Due": { label: "Due now", tone: "warning" },
  "Approaching Due": { label: "Due very soon", tone: "warning" },
  "Due Soon": { label: "Coming up", tone: "info" },
  "Not Due Yet": { label: "Stocked up", tone: "neutral" },
  "Lapsed": { label: "Haven't bought lately", tone: "muted" },
  "Insufficient History": { label: "Bought once", tone: "muted" },
};

/** Short timing line, e.g. "Due in 3 days", "Overdue by 5 days". */
export function timingText(rec) {
  const interval = rec.expected_purchase_interval;
  const since = rec.days_since_last_purchase;
  if (interval == null || since == null) {
    return since == null ? "" : `Last bought ${since === 0 ? "today" : `${plural(since, "day")} ago`}`;
  }
  const remaining = Math.round(interval - since);
  // Follow the engine's status so the badge and this line never disagree.
  if (rec.replenishment_status === "Lapsed") return `Last bought ${plural(since, "day")} ago`;
  if (rec.replenishment_status === "Due") return "Due now";
  if (remaining > 0) return `Due in ${plural(remaining, "day")}`;
  if (remaining === 0) return "Due today";
  return `Overdue by ${plural(-remaining, "day")}`;
}

export const discountPercent = (price, discounted) =>
  price && discounted && discounted < price ? Math.round((1 - discounted / price) * 100) : 0;
