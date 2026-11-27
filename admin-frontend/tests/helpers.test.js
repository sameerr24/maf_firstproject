import assert from "node:assert/strict";
import { test } from "node:test";
import { buildUrl, DEFAULT_API_URL } from "../src/api/url.js";
import { formatDate, formatDateTime, plural, signed, timeUntil } from "../src/format.js";
import { parseHash, toHash } from "../src/router.js";

test("buildUrl joins the base, /api/admin and non-empty query values", () => {
  assert.equal(buildUrl(undefined, "/at-risk"), `${DEFAULT_API_URL}/api/admin/at-risk`);
  assert.equal(buildUrl("http://localhost:5002/", "/inventory", { q: "milk tea", status: "", page: null }),
    "http://localhost:5002/api/admin/inventory?q=milk+tea");
  assert.equal(buildUrl(undefined, "/subscriptions", { page: 2 }), `${DEFAULT_API_URL}/api/admin/subscriptions?page=2`);
});

test("parseHash defaults to at-risk and reads filters", () => {
  assert.deepEqual(parseHash(""), { page: "at-risk", params: {} });
  assert.deepEqual(parseHash("#/nope"), { page: "at-risk", params: {} });
  assert.deepEqual(parseHash("#/subscriptions?product_id=bb-1&customer_id=CUST-0001"),
    { page: "subscriptions", params: { product_id: "bb-1", customer_id: "CUST-0001" } });
});

test("toHash round-trips and drops empty filters", () => {
  assert.equal(toHash("inventory"), "#/inventory");
  assert.equal(toHash("subscriptions", { product_id: "bb-1", q: "" }), "#/subscriptions?product_id=bb-1");
  assert.deepEqual(parseHash(toHash("subscriptions", { q: "a&b" })).params, { q: "a&b" });
});

test("dates and times in IST", () => {
  assert.equal(formatDate("2026-11-28"), "Sat, 28 Nov");
  assert.equal(formatDateTime("2026-11-27T12:30:00+00:00"), "6:00 PM, Fri 27 Nov");
  const now = Date.parse("2026-11-27T09:15:00+00:00");
  assert.equal(timeUntil("2026-11-27T12:30:00+00:00", now), "in 3h 15m");
  assert.equal(timeUntil("2026-11-29T12:30:00+00:00", now), "in 2d 3h");
  assert.equal(timeUntil("2026-11-27T09:20:00+00:00", now), "in 5m");
  assert.equal(timeUntil("2026-11-27T09:00:00+00:00", now), "passed");
});

test("small formatters", () => {
  assert.equal(plural(1, "delivery"), "1 delivery");
  assert.equal(plural(3, "unit"), "3 units");
  assert.equal(signed(5), "+5");
  assert.equal(signed(-3), "-3");
});
