// Dates and times shown in the business timezone (Asia/Kolkata), as in the customer app.
const TZ = "Asia/Kolkata";

/** Weekday, day and month of a Date in a timezone, from parts (locale punctuation varies). */
function dayParts(date, timeZone) {
  const parts = Object.fromEntries(new Intl.DateTimeFormat("en-GB", {
    weekday: "short", day: "2-digit", month: "short", timeZone,
  }).formatToParts(date).map((p) => [p.type, p.value]));
  return parts;
}

/** "2026-11-28" -> "Sat, 28 Nov" (a calendar date, no timezone shift). */
export function formatDate(isoDate) {
  if (!isoDate) return "-";
  const [y, m, d] = isoDate.split("-").map(Number);
  const p = dayParts(new Date(Date.UTC(y, m - 1, d)), "UTC");
  return `${p.weekday}, ${p.day} ${p.month}`;
}

/** ISO timestamp -> "6:00 PM, Fri 27 Nov" in IST. */
export function formatDateTime(iso) {
  if (!iso) return "-";
  const d = new Date(iso);
  const time = d.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit", hour12: true, timeZone: TZ });
  const p = dayParts(d, TZ);
  return `${time}, ${p.weekday} ${p.day} ${p.month}`;
}

/** Time left until an ISO moment: "in 3h 15m", "in 2d 4h", "passed". */
export function timeUntil(iso, now = Date.now()) {
  const ms = new Date(iso).getTime() - now;
  if (ms <= 0) return "passed";
  const mins = Math.floor(ms / 60000);
  const days = Math.floor(mins / 1440), hours = Math.floor((mins % 1440) / 60), minutes = mins % 60;
  if (days) return `in ${days}d ${hours}h`;
  if (hours) return `in ${hours}h ${minutes}m`;
  return `in ${minutes}m`;
}

export function formatPrice(value) {
  return `₹${Number(value).toFixed(2)}`;
}

export function plural(n, word) {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}

/** "+5" / "-3" */
export function signed(n) {
  return n > 0 ? `+${n}` : String(n);
}

/** "2026-11-28" + 2 -> "2026-11-30" (calendar dates, no timezone). */
export function addDays(isoDate, days) {
  const [y, m, d] = isoDate.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d + days)).toISOString().slice(0, 10);
}

/** "every day", "every week", "every 3 days" (same wording as the Telegram message). */
export function frequencyText(days) {
  return days === 1 ? "every day" : days === 7 ? "every week" : `every ${days} days`;
}
