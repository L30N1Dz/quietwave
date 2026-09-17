<div align="center">

# QUIETWAVE

**A self-hosted station for one phone number.**

Send and receive SMS and MMS through a Twilio number, from any device with a
browser and Wi-Fi. No carrier service required on the phone itself.

</div>

---

## What this actually is

A small FastAPI service plus an installable web app. It gives a phone with no
cellular plan a working text-messaging app: the phone talks to this server over
Wi-Fi, and the server talks to Twilio, which talks to the carrier network.

It was built for a specific situation — a kid with a Wi-Fi-only phone who
wants to text her friend — and it is deliberately small. One passphrase, one
or more Twilio numbers, a thread list, a conversation view, a compose box.

**It only touches the Messaging half of a Twilio number.** If that number is
also handling voice through a SIP trunk or a PBX, QUIETWAVE leaves that alone.
Twilio configures Voice and Messaging independently per number, so pointing the
messaging webhook here cannot reroute or break your calls. The setup
instructions below never ask you to touch Voice Configuration.

### What it is not

- Not end-to-end encrypted. It is SMS. Carriers and Twilio can read it.
- Not a multi-user chat system. One shared passphrase opens the station.
- Not free to run. Twilio bills per message segment and per attachment.

---

## How it works

```
  friend's phone                                       her phone
        │                                                   ▲
        │ SMS                                               │ Wi-Fi
        ▼                                                   │
   carrier network                                   ┌──────┴──────┐
        │                                            │  QUIETWAVE  │
        ▼                                            │   web app   │
     Twilio  ──── webhook (POST, signed) ───────────▶└──────┬──────┘
        ▲                                                   │
        └──────── REST API (outbound send) ─────────────────┘
                                                            │
                                                     ┌──────┴──────┐
                                                     │   MongoDB   │
                                                     │  + media on │
                                                     │    disk     │
                                                     └─────────────┘
```

**Inbound.** Twilio POSTs to `/webhooks/twilio/sms`. The signature is verified,
the message is stored, and an empty `204` goes back — no TwiML, so Twilio never
auto-replies. Attachments are downloaded in a background task so the webhook
returns well inside Twilio's timeout.

**Outbound.** The browser posts to the API; the server calls Twilio. Your
account credentials stay in the server process and never reach the client.

**Attachments.** Inbound media URLs from Twilio require your account
credentials to fetch, so they are downloaded once on receipt and afterwards
served from this app behind the session guard. Outbound attachments are the
mirror image: Twilio has to fetch them over the public internet and cannot log
in, so each one gets an unguessable, expiring URL that is only ever handed to
Twilio.

---

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/)
- MongoDB 5+ (a `quietwave` database is created on first run)
- A Twilio account with an SMS-capable number
- A way to expose one local port publicly over HTTPS — these instructions use
  Cloudflare Tunnel

---

## Try it without any of that

Before wiring up Twilio, you can run the interface on fake data:

```bash
uv run python -m app.cli demo
```

Open <http://127.0.0.1:8090>, type anything as the passphrase, and click
through. No Twilio account, no MongoDB, nothing persisted. This is also the
easiest way to compare the four [themes](#themes).

---

## Setting it up properly

### 1. Install

```bash
git clone https://github.com/L30N1Dz/quietwave.git
cd quietwave
uv sync
```

### 2. Configure

```bash
cp .env.example .env
uv run python -m app.cli secret
```

Put that generated value in `SESSION_SECRET`, then fill in the rest of `.env`:
your Twilio SID and auth token, the public HTTPS URL this app will be reachable
at, and the passphrase that opens the station.

`.env` is gitignored. Keep it that way.

### 3. Check your work

```bash
uv run python -m app.cli check
```

This prints the configuration, tells you the exact URLs to paste into Twilio,
verifies the database is reachable, and lists anything that will stop the app
from starting.

### 4. Add your number

```bash
uv run python -m app.cli add-line +15551234567 --label "Her line"
```

### 5. Run it

```bash
uv run python -m app.cli serve
```

---

## Pointing Twilio at it

In the Twilio console, go to **Phone Numbers → Manage → Active numbers** and
click the number you are activating. Scroll to **Messaging Configuration**.

| Field | Value |
|---|---|
| A message comes in | **Webhook**, `HTTP POST`, `https://YOUR-DOMAIN/webhooks/twilio/sms` |
| Status callback URL | `https://YOUR-DOMAIN/webhooks/twilio/status` |

> **Leave Voice Configuration completely alone.** If that number points at a
> SIP trunk or a PBX, it keeps doing so. Messaging and Voice are independent
> per number; changing one does not affect the other.

### A2P 10DLC — read this before you conclude it is broken

US carriers filter application-to-person traffic from unregistered long codes,
and Twilio-originated SMS counts as A2P even when a human is typing every word.

If messages send successfully but never arrive, or you see error **30007** or
**30034** on a message, the number needs A2P 10DLC brand and campaign
registration in the Twilio console. This is a Twilio/carrier requirement and
nothing in this codebase can work around it. QUIETWAVE surfaces both error
codes with a plain-English explanation rather than a bare number.

---

## Deploying

The `deploy/` directory has a systemd unit and a Cloudflare Tunnel ingress
snippet, with setup instructions in [deploy/README.md](deploy/README.md).

The short version: run QUIETWAVE bound to `127.0.0.1`, put a Cloudflare Tunnel
in front of it, and point `PUBLIC_BASE_URL` at the public hostname.

### Why `PUBLIC_BASE_URL` matters so much

Twilio signs each webhook against the exact URL it called. Behind a tunnel or
reverse proxy, the request that arrives at the app has a different scheme, host
and port than the one Twilio signed. QUIETWAVE therefore builds the URL it
validates against from `PUBLIC_BASE_URL` plus the known route path, and never
from the incoming request.

If `PUBLIC_BASE_URL` does not exactly match the URL configured in Twilio, every
inbound message will be rejected with a `403`. That is the single most common
setup failure, and it is a one-line fix.

---

## Themes

Four, switchable from inside the app (**Station → Band**) and settable as the
server default with `QUIETWAVE_THEME`:

| | Theme | Look |
|---|---|---|
| 🟠 | **QUIETWAVE** | Clandestine numbers station. Amber phosphor on dead black, monospaced, scanlines. Conversations are *channels*, send is *TRANSMIT*. |
| 🔵 | **RELAY NINE** | Derelict deep-space relay. Ice cyan on navy, soft panels, star grid. Conversations are *uplinks*, messages are *packets*. |
| 🟤 | **EMBERLINK** | Scavenged mesh after the grid went down. Ember and bone on charred brown, condensed type. Conversations are *links*, people are *nodes*. |
| 🟣 | **NEONWIRE** | Megacorp comms terminal. Magenta and cyan on violet-black, heavy bloom. Channels are *licensed*, users are *citizens*. |

A theme is two data files — a set of CSS custom properties and a lexicon of
every user-visible string — and no structural CSS of its own. Adding a fifth is
a folder, not a fork. See [docs/THEMES.md](docs/THEMES.md).

---

## Configuration

Everything lives in `.env`; see [.env.example](.env.example) for the annotated
version.

| Variable | Default | Notes |
|---|---|---|
| `TWILIO_ACCOUNT_SID` | — | Required |
| `TWILIO_AUTH_TOKEN` | — | Required. Also signs webhook validation |
| `PUBLIC_BASE_URL` | `http://127.0.0.1:8090` | Must match Twilio exactly |
| `APP_PASSPHRASE` | — | Opens the station |
| `SESSION_SECRET` | — | Signs the session cookie |
| `MONGO_URI` | `mongodb://localhost:27017` | Credentials go here if auth is on |
| `MONGO_DB_NAME` | `quietwave` | |
| `QUIETWAVE_STORE` | `mongo` | `memory` for demos and tests |
| `QUIETWAVE_MEDIA_ROOT` | `./data/media` | Attachment storage |
| `QUIETWAVE_THEME` | `quietwave` | Server default theme |
| `QUIETWAVE_HOST` / `_PORT` | `127.0.0.1` / `8090` | |
| `QUIETWAVE_POLL_INTERVAL_MS` | `5000` | How often the client checks for traffic |
| `QUIETWAVE_VALIDATE_TWILIO_SIGNATURE` | `true` | Never `false` in production |
| `QUIETWAVE_DEFAULT_COUNTRY_CODE` | `1` | Assumed when a number omits one |
| `QUIETWAVE_OUTBOUND_MEDIA_TTL_HOURS` | `24` | Lifetime of outbound media URLs |
| `QUIETWAVE_SESSION_TTL_DAYS` | `180` | So she types the passphrase rarely |
| `QUIETWAVE_COOKIE_SECURE` | `true` | `false` only for plain-HTTP localhost |
| `QUIETWAVE_MAX_UPLOAD_BYTES` | `5242880` | Twilio's MMS ceiling is 5 MB |

### Command line

```bash
uv run python -m app.cli check                  # config + connectivity report
uv run python -m app.cli add-line +1555...      # provision a number
uv run python -m app.cli list-lines
uv run python -m app.cli set-line-active +1555... --off
uv run python -m app.cli secret                 # generate a SESSION_SECRET
uv run python -m app.cli serve
uv run python -m app.cli demo                   # fake data, no Twilio
```

---

## Security

What is handled:

- Inbound webhooks are verified against Twilio's signature, built from
  configuration rather than the proxied request.
- The whole UI API sits behind a session guard; login is rate-limited.
- Twilio credentials never leave the server. Stored file paths, Twilio media
  URLs and outbound media tokens are stripped from every API response.
- Attachment paths are resolved against the media root and cannot escape it.
- A strict Content-Security-Policy, and no user-supplied text is ever written
  to the DOM as HTML.

What is on you:

- **Put a passphrase on MongoDB.** A default MongoDB install listening on a LAN
  interface has no authentication at all, which means every message stored by
  this app is readable by anything on the network. If your instance is exposed
  beyond `127.0.0.1`, enable auth and put the credentials in `MONGO_URI`.
- **Keep `.env` out of version control.** It is gitignored; do not force it in.
- **Use HTTPS.** Session cookies are `Secure` by default for a reason.
- Anyone with the passphrase reads every message. That is the intended design,
  not an oversight.

---

## Tests

```bash
uv run pytest
```

The suite covers webhook signature validation (including the
behind-a-proxy failure mode), inbound ingest and idempotency, the session gate
and login throttle, sending and failure recording, attachment handling in both
directions, path-traversal refusal, and theme/lexicon consistency.

Storage tests run against the in-memory backend by default. To run them against
a real MongoDB as well — which is what makes the in-memory tests trustworthy —
point them at a server:

```bash
QUIETWAVE_TEST_MONGO_URI=mongodb://localhost:27017 uv run pytest
```

Each run uses a uniquely named scratch database and drops it afterwards.

---

## Layout

```
app/
├── main.py            application assembly, security headers, shell
├── config.py          settings, startup validation
├── auth.py            passphrase, session, login throttle
├── twilio_client.py   signature validation + outbound gateway
├── ingest.py          inbound webhook -> stored history
├── outbound.py        stored history -> Twilio
├── media.py           attachments, both directions
├── phone.py           E.164 normalisation
├── cli.py             station management commands
├── routes/            webhooks, session, api, media
└── store/             Store protocol + Mongo and in-memory backends
static/
├── index.html         the shell
├── css/base.css       structure only, entirely theme-agnostic
├── js/                app, api client, theming
└── themes/<id>/       theme.css + lexicon.json per theme
deploy/                systemd unit + Cloudflare Tunnel config
docs/                  theme guide, original design scaffold
```

---

## License

MIT. See [LICENSE](LICENSE).
