# SCAFFOLD.md — Twilio Texting App

## Context

Self-hosted texting front-end for one or more of the 3 Twilio numbers, so a phone with no carrier service can send and receive SMS/MMS over Wi-Fi like a normal texting app. Runs alongside the other PBX-GHOST services. Voice on these numbers keeps routing through the existing Asterisk PBX exactly as it does today — this app only touches the **Messaging** side of the Twilio number config. Twilio treats Voice and Messaging configuration on a number as fully independent, so wiring this up cannot break the existing PBX call routing.

## Stack

- **Backend**: Python, FastAPI, `uv` for env/deps
- **DB**: MongoDB — new `texting_app` database on the existing instance (or its own, see Open Decisions)
- **Frontend**: installable PWA (manifest + service worker), plain JS to start — no build step, easy for Claude Code to iterate on directly
- **Messaging**: Twilio Programmable Messaging — inbound via webhook, outbound via the `twilio` Python SDK
- **Public exposure**: Cloudflare Tunnel — same pattern as the existing tunnel setup: pick a subdomain + local port (e.g. `text.<your-domain>` → `127.0.0.1:8090`), add a tunnel route + systemd unit
- **Process mgmt**: systemd unit, mirroring the existing `cloudflared` unit

## Architecture

- **Webhook Receiver** — `POST /webhooks/twilio/sms`. Twilio calls this on every inbound SMS/MMS. Validates `X-Twilio-Signature`, writes the message to Mongo, returns an empty 200 (no auto-reply TwiML).
- **Outbound Sender** — thin wrapper around `twilio.rest.Client.messages.create()`. Called only from the API layer — Twilio credentials never reach the browser.
- **State Store** — MongoDB collections `lines`, `threads`, `messages` (schema below). Threads are keyed by `(line_id, counterpart_number)` so history is per-conversation, per-number.
- **Media Proxy** — Twilio's `MediaUrl0..N` require Twilio's own Basic Auth to fetch and can't be handed to the browser directly. Download once on receipt and store locally (local disk under the app's data dir, or Mongo GridFS) rather than re-proxying Twilio on every view.
- **Auth Gate** — the webhook route is authenticated by Twilio's signature; the human-facing UI needs its own separate login (see Open Decisions). Simplest version: one shared passphrase per line, session cookie.
- **PWA Client** — thread list, thread view, compose box. Phase 1 polls for new messages; Phase 3 upgrades to Web Push.

## Data model (MongoDB)

```
lines
  _id
  twilio_number       "+15551234567"     (E.164)
  label               "Daughter's line"
  active              true

threads
  _id
  line_id             -> lines._id
  counterpart_number  "+15559876543"
  counterpart_label   "Friend's name"    (editable, optional)
  last_message_at
  unread_count

messages
  _id
  thread_id           -> threads._id
  direction           "inbound" | "outbound"
  body
  media               [{ path, content_type }]   (local refs, never raw Twilio URLs)
  twilio_sid
  status              "received" | "queued" | "sent" | "delivered" | "failed"
  created_at
```

## API surface

| Route | Method | Purpose |
|---|---|---|
| `/webhooks/twilio/sms` | POST | Twilio inbound webhook (form-encoded, signature-validated) |
| `/api/threads` | GET | List threads for the logged-in line(s) |
| `/api/threads` | POST | Start a new thread (given a counterpart number) |
| `/api/threads/{id}/messages` | GET | Message history for a thread |
| `/api/threads/{id}/messages` | POST | Send a message (body + optional media) |
| `/api/media/{message_id}/{idx}` | GET | Serve a stored media file |
| `/auth/login`, `/auth/logout` | POST | Session auth for the PWA |

## Twilio-side setup (per number you activate)

1. Console → Phone Numbers → the number → **Messaging Configuration**.
2. "A message comes in" → Webhook, `POST`, `https://text.<your-domain>/webhooks/twilio/sms`.
3. Leave **Voice Configuration** untouched — it keeps pointing at the SIP trunk / Asterisk, unaffected by step 2.

## Directory layout

```
twilio-texting-app/
├── SCAFFOLD.md
├── pyproject.toml
├── .env.example
├── app/
│   ├── main.py
│   ├── config.py
│   ├── db.py
│   ├── models.py
│   ├── twilio_client.py
│   ├── webhooks.py
│   ├── api.py
│   ├── auth.py
│   └── media.py
├── static/
│   ├── index.html
│   ├── manifest.json
│   ├── service-worker.js
│   ├── app.js
│   └── styles.css
└── deploy/
    ├── twilio-texting-app.service
    └── cloudflared-route-snippet.yml
```

## Environment variables

```
TWILIO_ACCOUNT_SID=
TWILIO_AUTH_TOKEN=
PUBLIC_BASE_URL=https://text.<your-domain>     # used to validate signatures — see gotcha below
MONGO_URI=mongodb://localhost:27017
MONGO_DB_NAME=texting_app
SESSION_SECRET=
```

## Phased build plan

1. **Core send/receive** — one hardcoded line, webhook + signature validation, outbound send, polling PWA (thread list, thread view, compose). Text only, no media.
2. **MMS + multi-line** — inbound/outbound media handling, `lines` becomes a real collection instead of a single env var, thread creation picks a line.
3. **Real-time push** — VAPID keys, service worker push handler, subscribe-on-install flow, badge counts.
4. **Ops hardening** — systemd unit + Cloudflare Tunnel route wired up, Twilio status-callback handling for delivery states, basic backup of the Mongo collections.

## Gotchas worth flagging up front

- **Signature validation behind Cloudflare Tunnel**: don't reconstruct the request URL from inside FastAPI. Build it from the fixed `PUBLIC_BASE_URL` env var + the known route path, and pass *that* to `twilio.request_validator.RequestValidator.validate()`. A proxied request's internal URL won't reliably match what Twilio actually signed against.
- **Media needs auth**: `MediaUrl0..N` require Twilio Basic Auth (account SID/token) to fetch — never pass them straight to the frontend. Download once on receipt and serve from local storage.
- **iOS push is Home-Screen-only**: Safari only exposes the Push API to a PWA that's already been added to the Home Screen — a page open in a normal Safari tab can't subscribe at all, no workaround. Phase 3 should prompt "Add to Home Screen" on iOS *before* asking for notification permission.
- **Twilio's webhook parameter set can grow over time** — don't hardcode a strict allow-list of expected form fields in the webhook handler; read what's there and ignore the rest.

## Open decisions (confirm before or while building)

- Which of the 3 numbers is Phase 1's line, and what subdomain to use (existing domain vs. a new one)
- Auth model: one shared passphrase for now, or real per-user accounts from the start
- Whether any kind of parent-visible view/export is wanted, or this is just her line, full stop
- Mongo: new dedicated database, or a collection set inside an existing database
