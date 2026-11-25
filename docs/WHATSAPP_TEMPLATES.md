# WhatsApp message templates

Business-initiated messages (those sent when the customer hasn't written to us in the last 24 hours) must use templates approved by Meta. This module sends three. Submit them in **WhatsApp Manager → Message templates → Create template**, using the exact names, variable order and button order below. The code checks that each message matches this order (`TEMPLATE_BODY_PARAMS` and `TEMPLATE_BUTTONS` in `app/notifications/sender.py`). If you change a template here, change it there too.

Settings for all three:
- **Category:** Utility. They're about the customer's own subscription and contain no promotional wording, which keeps Meta from recategorising them as Marketing.
- **Language:** English (`en`).
- **Variables:** numbered (`{{1}}`, `{{2}}`, ...). Meta needs a sample value for each one.

Follow-up messages after the customer taps a button (the slot picker, skip/delay choices, "Are you sure?", confirmations) are free-form interactive messages inside the 24-hour customer service window. They need no template.

---

## 1. `subscription_delivery_reminder`

Sent the day before each delivery. A fresh reminder is also sent after the customer delays a delivery, because every charge must be approved.

**Header (text):**
```
Delivery reminder
```

**Body:**
```
Your subscription delivery of {{1}} x {{2}} is scheduled for {{3}}.

Total: {{4}}

Please approve to confirm this delivery. You will not be charged unless you approve. If we don't hear from you by {{5}}, this delivery will be skipped.
```

**Footer:**
```
Smart Reorder subscriptions
```

**Buttons (Quick reply, in this order):**

| # | Button text | Payload sent by the code |
|---|---|---|
| 1 | `Approve` | `APPROVE:<occurrence_id>` |
| 2 | `Skip or delay` | `SKIP:<occurrence_id>` |
| 3 | `Stop subscription` | `STOP:<occurrence_id>` |

**Variables:**

| Var | Code name | Sample value |
|---|---|---|
| `{{1}}` | quantity | `2` |
| `{{2}}` | product | `GoodLife UHT Treated Toned Milk` |
| `{{3}}` | date | `Sat, 28 Nov` |
| `{{4}}` | price | `₹56.00` |
| `{{5}}` | reply_by | `9:00 PM on Fri, 27 Nov` |

When the subscriber price has changed since subscribing, `{{4}}` reads e.g. `₹57.00 (price changed, was ₹54.00)`. Approving means accepting the new price.

---

## 2. `delivery_skipped_notice`

Sent when a reminder wasn't answered by its deadline and the delivery was skipped automatically.

**Body:**
```
Your {{1}} delivery for {{2}} was skipped because we did not receive your approval in time. You have not been charged.

Your next delivery is scheduled for {{3}}. We will send you a reminder before then.
```

**Footer:**
```
Smart Reorder subscriptions
```

**Buttons:** none

**Variables:**

| Var | Code name | Sample value |
|---|---|---|
| `{{1}}` | product | `GoodLife UHT Treated Toned Milk` |
| `{{2}}` | date | `Sat, 28 Nov` |
| `{{3}}` | next_date | `Sat, 05 Dec` |

---

## 3. `delivery_out_of_stock_notice`

Sent instead of a reminder when there isn't enough stock for the delivery.

**Body:**
```
Sorry, {{1}} is out of stock, so we could not schedule your delivery of {{2}} for {{3}}. You have not been charged.

Your next delivery is scheduled for {{4}}. We will send you a reminder before then.
```

**Footer:**
```
Smart Reorder subscriptions
```

**Buttons:** none

**Variables:**

| Var | Code name | Sample value |
|---|---|---|
| `{{1}}` | product | `Bread` |
| `{{2}}` | quantity | `1` |
| `{{3}}` | date | `Sat, 28 Nov` |
| `{{4}}` | next_date | `Sat, 05 Dec` |

---

## Submission checklist

- [ ] The names match exactly (lowercase, underscores).
- [ ] The category is **Utility** and the language is **English (`en`)**. The code must later send the same language code.
- [ ] The variables are in the order above, each with its sample value.
- [ ] The reminder has its 3 quick-reply buttons in the order above. The code fills in the payloads per message.
- [ ] No body starts or ends with a variable, and no two variables are directly next to each other. These drafts already follow both rules.
- [ ] After approval, check that each template's status is **Approved** in WhatsApp Manager before testing. Approval can take anywhere from minutes to about a day, and Meta may ask for edits.
