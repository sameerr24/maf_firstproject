"""Values shared by the subscription module (API, scheduler, WhatsApp flows, seed data)."""

# Delivery slots offered in the WhatsApp slot picker and stored in
# orders.delivery_slot / delivery_occurrences.delivery_slot / subscriptions.preferred_slot.
DELIVERY_SLOTS = ("07:00-09:00", "09:00-12:00", "12:00-15:00", "15:00-18:00", "18:00-21:00")
