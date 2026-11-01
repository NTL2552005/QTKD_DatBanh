# BiBi Cake AI assistant (milestone 5.3)

## Scope and trust boundaries

The assistant is an optional, authenticated CUSTOMER feature. It can discuss public cake
catalog entries, quote only published store-policy sources, ask follow-up questions, and
save a validated draft. It cannot call business tools or create an order. The customer must
review and submit the existing order form; the existing order service rechecks catalog
availability, snapshots current data, and enforces request-key idempotency. STAFF still
checks feasibility and sets the quote; OWNER controls published policy content.

The assistant must not confirm orders or payments, set prices, promise a delivery slot or
ingredient availability, approve photos, process refunds, or read order/customer records.
Allergy-related conversations receive a local handoff warning; no ingredient-safety claim is
made. Provider output is validated against the retrieved IDs and schema; unsupported claims
that an order/payment was confirmed, a discount granted, or availability/delivery guaranteed
are replaced with a staff handoff. No business write tool is exposed. Chat only saves validated
`AIDraft` fields; the customer must review and submit the existing order form.

The chat page explains that relevant conversation content and retrieved public data are sent
to the AI provider. Email, phone, address/recipient-name fields, and explicitly labeled
password, OTP, and payment-card values are redacted before provider calls. Do not enter these
details in chat; enter order contact information only in the order form.

## Retrieval and data

- Cake suggestions are queried from `catalog.CakeTemplate.objects.public()`, capped at five.
  Backend filters the stated maximum budget first, then ranks text matches over name,
  category, occasion/flavor/style metadata, and description. Serving data affects ranking
  only when marked verified. No external search or vector database is used.
- OWNER may maintain optional occasion, flavor, style, serving, ingredient, and allergen
  information on each cake. `advisory_data_verified` is false by default. Demo suggestions
  are explicitly labeled unverified and may only be described as ideas to confirm with staff.
  OWNER checks the verification box only after reviewing all advisory data. Editing advisory
  data automatically clears verification. Missing serving data is never inferred from cake
  size, name, or price.
- Policy retrieval reads only `assistant.StorePolicy` rows with `is_published=True`, ranked
  by relevant title/content matches and capped at four. Draft and unpublished policies are
  excluded on every request; there is no policy cache. OWNER manages policies at
  `/owner/assistant/policies/`. Editing a published title/body creates a new unpublished
  revision; OWNER must review and publish it separately.
- The provider returns source IDs, not links. The backend verifies every ID against the
  retrieved result set. Product links use trusted catalog slugs; published policy links use
  the backend policy-detail route and only resolve while the policy is currently published.
  Source policy version metadata is snapshotted with each answer.
- Only a bounded number of recent messages and bounded catalog/policy excerpts are sent.
  Provider telemetry stores request ID, provider/model, response duration, tokens when
  available, status, handoff/schema flags, and a sanitized error category—not raw prompts,
  API keys, or authorization headers.
- Search is database text matching with relevance ranking and a small set of explicit topic
  aliases. It is not semantic search and is not proof that Gemini will always follow policy.

## Provider and configuration

The default provider uses Google's official `google-genai` Python SDK and Gemini's
`generate_content` API with JSON structured output. `GEMINI_API_KEY` and `GEMINI_MODEL` are
read only by Django on the server. The selected default model is the stable
`gemini-3.8-flash`, which supports structured output according to the
[official model catalog](https://ai.google.dev/gemini-api/docs/models) and
[structured output guide](https://ai.google.dev/gemini-api/docs/structured-output).
The adapter sends retrieved catalog/published-policy context and bounded, contact-redacted
chat history, with no business tools or web search.
The official catalog was checked on 2026-10-31; access to a particular model by a specific
API account is not verified by that catalog or by mocked tests.

AI is off by default. Open the existing local `.env` without replacing any other settings.
Enter a newly created API key in `GEMINI_API_KEY`; `GEMINI_MODEL` may be left blank to use
`gemini-3.8-flash` or set explicitly to that model. Set `AI_ENABLED=True` to enable chat.
`AI_PROVIDER` defaults to `gemini`. Restart Django after changing `.env`. Never put keys in
source control, browser code, logs, screenshots, or chat messages. The SDK timeout is bounded
by `AI_TIMEOUT_SECONDS`, and SDK retries are disabled so a provider error falls back promptly.
Do not reuse any key previously pasted into a conversation; revoke/rotate it with Google and
enter a newly created key yourself in the local `.env`. Never print or share `.env`.
The existing OpenAI adapter remains available only when explicitly selected with
`AI_PROVIDER=openai` and its legacy `AI_API_KEY`/`AI_MODEL` settings.

The checked-in `.env.example` contains sample values only and a blank Gemini key. Preserve any
existing local `.env` settings; do not print or disclose its contents. The model setting defaults
to `gemini-3.8-flash`.

Authentication failures, rate limits, provider errors, timeouts, and invalid JSON are
categorized without logging API credentials or provider response bodies. When AI is disabled
or the key/model is missing, authenticated customers can still use clearly labeled local
catalog/published-policy lookup; natural-language generation and drafts require the provider.
The normal order form remains available. If a configured provider fails, relevant catalog/policy questions use a clearly labeled local
database lookup; budget results are limited to public, available cakes whose reference price is
at or below the stated amount. If no source matches, the user is told that staff must check.
Fallbacks do not create drafts/orders or infer portions, stock, a delivery slot, a custom quote,
discount, or fee. Fallback responses are recorded separately with the provider error category.
The AI switch does not disable ordinary ordering or the workflow webhook.

## Conversation, draft, and policy URLs

- `/assistant/` — public information page; authenticated CUSTOMER can start a session.
- `/assistant/<session-uuid>/` — private conversation, drafts, source labels, and validated
  suggestions. Other customers receive 404; non-CUSTOMER roles receive 403.
- `/assistant/policies/<code>/` — current published policy detail; drafts and hidden policies
  return 404.
- `/owner/assistant/policies/` — OWNER-only policy list and publishing controls.
- `/orders/new/<cake-slug>/?draft=<draft-uuid>` — prefilled existing order form. Recipient
  details are entered there. A draft can be linked to only one order, and a retired cake is
  rejected by the normal order service.

OWNER can initialize suggested, unverified metadata for the built-in demo catalog with:

```powershell
.\.venv\Scripts\python.exe manage.py seed_demo_advisory_data
```

The command only fills blank fields, never overwrites existing OWNER values, and leaves
serving, ingredient, and allergen data blank. All demo metadata remains unverified until OWNER
reviews the cake form at `/owner/catalog/cakes/` and explicitly confirms it.

The current SQLite tables are `assistant_chatsession`, `assistant_chatmessage`,
`assistant_aidraft`, `assistant_storepolicy`, and `assistant_airequestlog`.

### Publishing a policy for a demo

Create the suggested demo records with:

```powershell
.\.venv\Scripts\python.exe manage.py seed_demo_policies
```

The command requires an existing OWNER and creates `CS01`–`CS12` as drafts. It reports created
and skipped codes and each skipped row's existing publication state. Re-running does not
duplicate, publish, or overwrite existing policies. It also leaves all unrelated policies
unchanged. This content is a proposal for a school demo, not a real store-approved policy.

Sign in as an OWNER and open `/owner/assistant/policies/`. Review each draft and correct it
against the store's actual procedures before using the POST/CSRF **Công bố** action. Editing a
published title/content automatically returns the new version to draft; publish it again only
after review. Use **Gỡ công bố** to remove a policy from subsequent retrieval. No policy is
automatically published by the command. Do not add thresholds or promises not approved by the
store. With no relevant published source, the assistant says it cannot verify the policy.

The demo policy text mentions features/time rules that may not be implemented. In particular,
the 30-minute photo reminder is not automated, and the general customer order-cancellation
request described in CS09 is not a complete customer-facing workflow. Payment records are
manual; a transfer notice is not bank verification. OWNER must not publish those claims as
operational guarantees unless the corresponding workflow is added and verified.

### Real-provider demo checklist

After configuring a valid key/model and restarting Django, sign in as a CUSTOMER and use
`/assistant/`. Check each case manually against the current database and policies approved by
the store:

- Ask for cake suggestions within a budget; each suggestion must exist in the public catalog.
- Compare displayed reference prices with the current catalog database values.
- Give an ambiguous delivery date/time; the assistant should ask for clarification instead
  of turning it into a definite draft value.
- Ask about a policy published by OWNER and verify its source is shown.
- Unpublish or edit a policy, then verify the next chat turn no longer cites the stale version.
- Ask about a policy with no published source and verify the assistant says there is no
  information instead of inventing a rule.
- Prepare a draft and verify no order exists yet.
- Use the draft to prefill the existing order form; only submitting that form creates an order.
- Ask for a discount or payment confirmation; the assistant must not grant discounts or
  confirm payment.
- If the provider is unavailable or rejects a request, verify the labeled local lookup uses
  only currently published policies/public catalog items, or offers retry when no source fits.

Model wording and behavior can vary. This is a manual demo checklist, not proof that a live
model will always satisfy every case. Do not use real customer personal data in demo prompts.

## Output validation and fixed evaluation cases

The response schema is checked server-side even when the provider promises constrained JSON.
The backend rejects unexpected fields, invalid enums, unverified cake/policy IDs, invalid
quantities/budgets, overly long strings, model-generated URLs, and malformed confidence.
Dates and times must be exact ISO values, both present, and in the future; vague dates are
shown as fields to clarify, not copied into an order time. Confidence is uncalibrated model
output and is used only to choose a conservative handoff; it never confirms an order.
Templates autoescape assistant and customer text.

The mock-based test suite verifies policy seeding, role/CSRF checks, hidden-policy exclusion,
retrieval updates, local provider fallback, privacy redaction, and no order creation by chat.
These are deterministic backend guarantees; model behavior itself is not established by
keyword tests or mocks.

The mock-based test suite covers:

1. Real/public catalog retrieval and exclusion of unavailable cakes.
2. No published policy, unverified source IDs, and policy version labels.
3. Missing and ambiguous dates, past dates, invalid quantities, and malformed output.
4. Prompt-injection text, unsupported payment/order actions, and allergy handoff.
5. Authentication failure, bounded transient retry, and safe user-facing errors.
6. Contact-data redaction, private sessions, CUSTOMER/OWNER/STAFF permissions, and CSRF.
7. Escaped HTML, database rate limiting, concurrent/stale request leases, and duplicate IDs.
8. Prefilling the existing form, no order before submission, one-time draft linking, and
   rechecking a cake that stops accepting orders.
9. Ordinary catalog/order access while AI is disabled.

All provider requests in tests are mocked. No real API call, email, webhook, or order is made.
Real-provider latency, model behavior, token use, and account-level model availability remain
unverified until a key is configured by the project owner.
