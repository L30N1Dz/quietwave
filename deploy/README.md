# Deploying QUIETWAVE

The shape: QUIETWAVE listens on `127.0.0.1:8090`, systemd keeps it running, and
Cloudflare Tunnel provides the public HTTPS hostname that Twilio can reach. The
app is never exposed directly.

```
Twilio ──https──▶ Cloudflare edge ──tunnel──▶ cloudflared ──▶ 127.0.0.1:8090
```

---

## 1. Put the code somewhere sensible

```bash
sudo useradd --system --create-home --home-dir /opt/quietwave quietwave
sudo -u quietwave git clone https://github.com/L30N1Dz/quietwave.git /opt/quietwave
cd /opt/quietwave
sudo -u quietwave uv sync --frozen
```

## 2. Configure

```bash
sudo -u quietwave cp .env.example .env
sudo -u quietwave uv run python -m app.cli secret   # -> SESSION_SECRET
sudo -u quietwave nano .env
sudo chmod 600 .env
```

`PUBLIC_BASE_URL` must be the exact public hostname, with `https://` and no
trailing path. Everything about inbound message delivery depends on it.

```bash
sudo -u quietwave uv run python -m app.cli check
```

Fix anything it reports before continuing.

## 3. Install the service

```bash
sudo cp deploy/quietwave.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now quietwave
curl -s localhost:8090/healthz
```

Expect `{"status":"ok","store":"mongo","store_reachable":true}`.

## 4. Add the tunnel route

Merge the ingress entry from [cloudflared-ingress.yml](cloudflared-ingress.yml)
into your existing `/etc/cloudflared/config.yml`, above the catch-all rule.

```bash
cloudflared tunnel route dns YOUR-TUNNEL-ID text.example.com
sudo systemctl restart cloudflared
curl -s https://text.example.com/healthz
```

That last command must succeed from outside your network before Twilio has any
chance of delivering to it.

## 5. Provision the number and point Twilio at it

```bash
sudo -u quietwave uv run python -m app.cli add-line +15551234567 --label "Her line"
sudo -u quietwave uv run python -m app.cli check     # prints the exact webhook URLs
```

In the Twilio console, on that number, under **Messaging Configuration** only:

- **A message comes in** → Webhook, `HTTP POST`,
  `https://text.example.com/webhooks/twilio/sms`
- **Status callback URL** → `https://text.example.com/webhooks/twilio/status`

**Do not touch Voice Configuration.** If the number routes voice to a SIP trunk
or PBX, it keeps doing so — Twilio treats Voice and Messaging as independent
configuration on a number.

## 6. Install it on the phone

Open the site in Chrome on the phone, log in once, then use **Station →
Install to home screen** (or Chrome's ⋮ → *Add to Home screen*). The session
cookie lasts six months by default, so she should not have to type the
passphrase again for a long time.

---

## Backups

The message history is MongoDB; the attachments are files.

```bash
mongodump --uri="$MONGO_URI" --db=quietwave --out=/backup/quietwave-$(date +%F)
tar czf /backup/quietwave-media-$(date +%F).tar.gz -C /opt/quietwave/data media
```

---

## Troubleshooting

**Inbound messages never arrive, Twilio's debugger shows 403.**
`PUBLIC_BASE_URL` does not match the URL configured in Twilio. Twilio signs the
exact URL it calls, and QUIETWAVE validates against `PUBLIC_BASE_URL` plus the
route path. Compare them character by character, including `https://` and any
trailing slash. `app.cli check` prints what the app expects.

**Inbound messages never arrive, Twilio's debugger shows 11200 or a timeout.**
The tunnel is not reaching the app. Check `systemctl status quietwave`,
`systemctl status cloudflared`, and `curl https://YOUR-DOMAIN/healthz`.

**Outbound messages report `sent` but never arrive; error 30007 or 30034.**
Carrier filtering. The number needs A2P 10DLC registration in the Twilio
console. Nothing in this app can bypass it.

**Attachments show as "RECEIVING…" forever.**
The background download failed. Check `journalctl -u quietwave` — usually
credentials or a disk-permission problem on `QUIETWAVE_MEDIA_ROOT`.

**Outbound attachments fail.**
Twilio fetches them from `PUBLIC_BASE_URL/m/<token>` over the public internet.
That URL has to be publicly reachable; QUIETWAVE refuses to attempt it at all
when `PUBLIC_BASE_URL` is not `https://`.

**The app will not start.**
It refuses to boot on bad configuration and prints exactly what is wrong.
`journalctl -u quietwave -n 50` will show it.
