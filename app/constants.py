"""Values shared by the subscription module (API, scheduler, WhatsApp flows, seed data)."""

# Business timezone. Dates shown to customers ("today", delivery dates) and the naive
# orders.order_datetime are in this zone; timestamptz columns store UTC.
TIMEZONE = "Asia/Kolkata"

# Delivery slots offered in the WhatsApp slot picker and stored in
# orders.delivery_slot / delivery_occurrences.delivery_slot / subscriptions.preferred_slot.
DELIVERY_SLOTS = ("07:00-09:00", "09:00-12:00", "12:00-15:00", "15:00-18:00", "18:00-21:00")

# Subscription limits.
QUANTITY_MIN, QUANTITY_MAX = 1, 100
FREQUENCY_DAYS_MIN, FREQUENCY_DAYS_MAX = 1, 90

# Used when there is no recommendation for the customer + product.
DEFAULT_QUANTITY = 1
DEFAULT_FREQUENCY_DAYS = 7

# A delivery can be scheduled no sooner than this many days from today (leaves room for
# the ~24h WhatsApp reminder) and no later than MAX_SCHEDULE_AHEAD_DAYS.
MIN_LEAD_DAYS = 2
MAX_SCHEDULE_AHEAD_DAYS = 180
