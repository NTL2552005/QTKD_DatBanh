# BiBi Cake workflow webhook contract

## Responsibility boundary

Django owns order data, roles, and business decisions. The workflow may send
notifications only. It must not confirm orders or quotes, approve cake photos,
record payments, or change prices or user permissions. Email links lead to the
configured website; any approval or other state change must require sign-in and
a website POST protected by CSRF. A GET request must never perform approval.

## Delivery

Django writes an immutable event to the `orders_workflowevent` outbox in the
same database transaction as the business change. HTTP is performed only by
the bounded management command:

```powershell
.\.venv\Scripts\python.exe manage.py dispatch_workflow_events --limit 20
```

`WORKFLOW_ENABLED` defaults to `False`. Disabled dispatch leaves eligible
events pending. Configure the endpoint and bearer token through `.env`; do not
put secrets in source control or documentation. External webhook URLs must use
HTTPS. HTTP is allowed only for a loopback endpoint while Django `DEBUG` is
enabled. Redirects are not followed.

The sender limits the batch to 100 events, uses a 60-second lease and a
configurable request timeout of at most 30 seconds. Connection failures,
timeouts, HTTP 429, and HTTP 5xx are retried up to five times per automatic
retry cycle with increasing delay. A valid `Retry-After` is honored up to one
hour. Other 4xx responses, including 401/403, fail immediately and require
operator attention. OWNER can queue a failed event for another cycle from
`/owner/workflow-events/`; this POST does not send HTTP. Retry requests record
the OWNER and retain the original `event_id` and payload.

`delivered` means only that the webhook returned a 2xx response. It does not
mean an email was sent or reached the recipient. No callback from an email
provider is currently implemented.

## Request headers

```text
Content-Type: application/json; charset=utf-8
Authorization: Bearer <WORKFLOW_WEBHOOK_TOKEN>
X-Event-ID: <event_id>
Idempotency-Key: <event_id>
```

The workflow must authenticate the bearer token and persistently deduplicate
events by `event_id`. In-memory-only deduplication is insufficient. The workflow
should persist processing status and the final outcome before acknowledging
the webhook with 2xx.

## Payload

The payload is stored when Django creates the event and is not rebuilt on
retry. Timestamps are ISO 8601 with a timezone offset. `recipient_email` is
omitted if the customer has no valid email; Django records that event as failed
and does not send it to the webhook. Phone, delivery address, payment details,
secrets, and private photo URLs are never included.

Common fields:

| Field | Type | Meaning |
|---|---|---|
| `event_id` | string (UUID) | Stable idempotency key |
| `event_type` | string | Event name below |
| `schema_version` | integer | Payload schema version, currently `1` |
| `occurred_at` | string | Event creation time, ISO 8601 |
| `order_code` | string | Public-facing order code |
| `recipient_email` | string, optional | Customer email captured at event creation |
| `recipient_display_name` | string, optional | Customer display name, if present |
| `action_url` | string | Absolute URL from configured `SITE_BASE_URL` to the order detail |

### `order.confirmed`

Created when the customer accepts the current quote and the order changes to
confirmed. Includes `quote_version` (integer).

### `cake.review_requested`

Created for each eligible cake-photo version sent to the customer for review.
Includes `cake_review_version` (integer). The private photo is not attached and
its URL is not included.

### `order.completed`

Created when Django's delivery, payment, and open-incident rules move the order
to `HOAN_TAT`. Includes `completed_at` and `review_deadline` timestamps. The
24-hour deadline is a reminder/workflow milestone; it does not prevent the
customer from submitting a review after that time.

Sanitized example:

```json
{
  "event_id": "00000000-0000-4000-8000-000000000001",
  "event_type": "order.confirmed",
  "schema_version": 1,
  "occurred_at": "2026-10-30T20:00:00+07:00",
  "order_code": "BBC-EXAMPLE123",
  "recipient_email": "customer@example.invalid",
  "recipient_display_name": "Example Customer",
  "action_url": "https://cake.example.invalid/orders/mine/123/",
  "quote_version": 2
}
```

## Acknowledgement and duplicate-send limits

Return 2xx only after the workflow has durably accepted the event. Return 429
or 5xx for transient failures; Django applies bounded retry with backoff. Do
not return 2xx for an event that was discarded.

The outbox and `event_id` make webhook delivery retryable, but cannot guarantee
that a human receives exactly one email. If an email provider accepts a send
and the workflow crashes before persisting its outcome, a retry may send a
duplicate unless that provider supports its own idempotency key. The workflow
must use durable idempotency and document this remaining provider boundary.
