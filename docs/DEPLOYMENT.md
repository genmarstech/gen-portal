# Deploying the client portal

`app.genmars.co.ke` and `api.genmars.co.ke` on the shared Hetzner host.

> **This host is shared.** It already serves `genmars.co.ke` and
> `clipsserenityspa.co.ke` — a live client site. Every step below is written to
> be additive. Nothing here replaces `/etc/caddy/Caddyfile`, and nothing here
> touches another site's containers.

---

## Shape

```
  app.genmars.co.ke                       api.genmars.co.ke
  (the portal — the only host             (the same Django, for products,
   a signed-in browser talks to)           infrastructure, integrations)
          │                                        │
          └──────────── host Caddy (TLS, :443) ────┘
                    │                          │
          127.0.0.1:3010                127.0.0.1:8010
            web (Next.js)                  api (Django)
                    │                          │
                    └── /api/* server-side ────┘
                        (next.config.ts rewrites)
                                               │
                                      ┌────────┴────────┐
                                      db (Postgres)   redis
                             compose network only — no published ports
```

**The browser only ever talks to `app.genmars.co.ke`.** The portal's `/api/*`
calls are proxied server-side by Next, so page and API requests are
same-origin. `api.genmars.co.ke` is the same Django addressable in its own
right, for everything that is not this browser session.

That split is what keeps the auth design intact: no CORS anywhere, and the
session cookie stays host-only on `app.genmars.co.ke` — it is never sent to
`api.genmars.co.ke`, so a fault in a future public API endpoint cannot be
driven with a portal user's session.

> A browser calling `api.genmars.co.ke` directly would need CORS
> (`django-cors-headers`) and a deliberate decision about credentials. Do not
> add it by reflex to make one fetch work.

---

## First deploy

### 1. DNS

`A` records for **`app`** and **`api`** pointing at the host, both **DNS-only
(grey cloud)** in Cloudflare. Confirmed in place 2026-08-28.

Proxied would break two things at once: the ACME HTTP-01 challenge could not
reach the host, and `NUM_PROXIES = 1` would be wrong — DRF would read
Cloudflare's address as the client, collapsing every per-IP rate limit into one
global limit shared by all clients.

`genmars.co.ke` itself stays proxied. That is fine and unrelated: a static
marketing site with no rate limiting and a certificate long since issued.

```bash
dig +short app.genmars.co.ke
dig +short api.genmars.co.ke
```

### 2. Code and configuration

`/opt/gen-portal`, matching the rest of this host — `gen-website`, `clips-spa`
and the UrbanTrends projects all live under `/opt`. The systemd units and the
Caddy `root` directive hardcode this path, so a clone somewhere else needs those
edited too.

```bash
cd /opt
git clone git@github.com:genmarstech/gen-portal.git
cd gen-portal
```

Two env files, neither in the repository:

```bash
cp backend/.env.example backend/.env    # then fill it in
```

`backend/.env` needs a real `DJANGO_SECRET_KEY`, `DEBUG=False`, both hostnames
**and `127.0.0.1`** in `ALLOWED_HOSTS`, **both** origins in
`CSRF_TRUSTED_ORIGINS`, and a `RESEND_API_KEY`. A missing `api.genmars.co.ke`
in `ALLOWED_HOSTS` makes that hostname return 400 on every request, which reads
as a proxy fault rather than a settings one.

`ALLOWED_HOSTS` needs **four** values, and the two internal ones are the ones
that get left out:

```
ALLOWED_HOSTS=app.genmars.co.ke,api.genmars.co.ke,127.0.0.1,api
```

`127.0.0.1` — the image's `HEALTHCHECK` curls `http://127.0.0.1:8010/api/health`.
Without it Django answers its own probe with `400 DisallowedHost`, `api` never
reports healthy, and `web` never starts because it waits on it. The symptom is
a stack that looks like it is still booting, indefinitely.

`api` — Next's `/api/*` rewrite **rewrites the Host header to its destination**,
so Django sees `api:8010`, not the browser's `app.genmars.co.ke`. Without it
every sign-in, session check and dashboard load returns 400, while
`curl http://127.0.0.1:8010/api/health` on the host looks perfectly healthy.

Neither internal name admits anything from outside: both ports are published to
loopback only, `api` resolves solely on the compose network, and Caddy always
forwards the real Host for public traffic.

Mail goes through Resend over HTTPS, not SMTP — Hetzner blocks outbound mail
ports on new accounts, and that failure is a ten-second timeout per message
rather than anything that says "blocked". `EMAIL_BACKEND` and `RESEND_API_KEY`
are both checked at boot: with `DEBUG=False`, a backend that cannot send, or a
key that is empty, refuses to start.

**Before the first real sign-up, verify the domain in Resend and publish its
DNS records.** Until that is done every send returns 403 and no client can
finish creating an account. Add to what is already there, never replace it:
the existing SPF include and the `zmail` DKIM selector are what keep the human
mailbox at Zoho delivering. There must be exactly one SPF TXT record on the
domain — two is a permerror, and a permerror fails *both* senders.

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(64))"
```

And a top-level `.env` for compose, holding only the database credentials:

```bash
cat > .env <<'EOF'
POSTGRES_DB=genmars_portal
POSTGRES_USER=genmars
POSTGRES_PASSWORD=<generate one>
EOF
chmod 600 .env backend/.env
```

> `CSRF_TRUSTED_ORIGINS` is checked at boot: with `DEBUG=False` and an empty
> value, Django refuses to start. That is deliberate. Left unset it fails in the
> most misleading way available — sign-in still works, because DRF only enforces
> CSRF once a request is authenticated, so a smoke test passes while every
> authenticated write returns 403.

### 3. Check the host has room

This box already runs `clipsserenityspa.co.ke` (PHP + MariaDB) and the Genmars
marketing site. Every portal service has a memory ceiling — 512M api, 512M db,
384M web, 192M redis, **1.6G total** — precisely so ours cannot starve a live
client site. Confirm the host can carry that before starting:

```bash
free -h
df -h /
docker stats --no-stream --format 'table {{.Name}}	{{.MemUsage}}	{{.CPUPerc}}'
```

If free memory plus reclaimable cache is under roughly 2G, do not deploy until
the box is resized. An OOM kill does not politely pick our container.

### 4. Build and start

```bash
docker compose up -d --build
docker compose ps
```

All four services should be `healthy`. `api` will not start until `db` is,
because of the `condition: service_healthy` dependency.

### 5. Migrate and create the first staff account

```bash
docker compose exec api python manage.py migrate
docker compose exec api python manage.py createsuperuser
```

### 6. Static files for Caddy

`collectstatic` runs at image build time. Copy the result out of the image so
Caddy can serve it straight off disk:

```bash
docker compose cp api:/app/staticfiles ./staticfiles
ls staticfiles/admin/css/base.css   # must exist
```

**Repeat this on every deploy that changes static assets** — the copy is a
snapshot, not a live view.

The Caddy drop-in serves this directory with `handle_path /static/*` and
`root * /opt/gen-portal/staticfiles`. The prefix and the directory name differ
on purpose — `STATIC_URL` is `/static/`, collectstatic writes to
`staticfiles/` — and `handle_path` is what bridges them by stripping the
prefix. Changing it to plain `handle` makes Caddy look for
`/opt/gen-portal/static/`, which does not exist: the admin renders as unstyled
HTML, with a 404 in the browser console and nothing in the Caddy or Django logs
to explain it.

> Do not "simplify" this into a bind mount of `./staticfiles` onto
> `/app/staticfiles`. That masks the files the image already contains: on a
> fresh clone the host directory is empty, so the container serves nothing and
> the admin renders with no CSS, with nothing in any log to say why.

### 7. Caddy

```bash
sudo cp deploy/genmars-portal.caddy /etc/caddy/conf.d/genmars-portal.caddy
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

`caddy validate` passing is necessary but **not sufficient** — some failures
happen at load, not at adapt. Always check the reload actually took:

```bash
systemctl is-active caddy
journalctl -u caddy -n 30 --no-pager
```

> **First issuance on these new subdomains:** consider the staging CA block at
> the bottom of `deploy/genmars-portal.caddy`. Neither hostname has ever had a
> certificate. Let's Encrypt rate-limits failed
> issuance hard enough to cost a week. Scope it per-site — a global `acme_ca`
> would put a browser warning on the live client site sharing this host.

### 8. Backups

```bash
sudo cp deploy/genmars-portal-backup.* /etc/systemd/system/
sudo cp deploy/genmars-portal-restore-test.* /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now genmars-portal-backup.timer
sudo systemctl enable --now genmars-portal-restore-test.timer
systemctl list-timers 'genmars-portal-*'
```

Then **prove it before trusting it**:

```bash
./scripts/backup.sh
./scripts/restore-test.sh
```

The restore test creates a scratch database, restores the newest dump into it,
asserts every table the application needs exists and that there are actually
rows, then drops it. It is safe against production and never writes to the live
database.

### 9. Verify

```bash
# the portal
curl -I  https://app.genmars.co.ke/                   # 200
curl -s  https://app.genmars.co.ke/api/health         # {"status": "ok"} via Next
curl -I  http://app.genmars.co.ke/                    # 308 to https
curl -sI https://app.genmars.co.ke/static/admin/css/base.css | head -1

# the API in its own right
curl -s  https://api.genmars.co.ke/api/health         # {"status": "ok"}
curl -I  http://api.genmars.co.ke/                    # 308 to https

# certificates, and who issued them
for h in app.genmars.co.ke api.genmars.co.ke; do
  echo "== $h"
  echo | openssl s_client -connect "$h:443" -servername "$h" 2>/dev/null \
    | openssl x509 -noout -issuer -dates
done
```

Then sign in with a real browser and confirm the session cookie is `Secure`,
`HttpOnly`, `SameSite=Lax`. Settings claiming it is not the same as the browser
receiving it.

---

## Routine deploy

```bash
cd /opt/gen-portal
git pull
docker compose up -d --build
docker compose exec api python manage.py migrate
docker compose cp api:/app/staticfiles ./staticfiles   # if static changed
docker compose ps
curl -s https://app.genmars.co.ke/api/health
```

Take a backup **before** any deploy carrying a migration:

```bash
./scripts/backup.sh
```

---

## Rollback

### Code only, no migration

The fastest correct move. Every image is tagged, so go back to the previous one:

```bash
cd /opt/gen-portal
git log --oneline -5
git checkout <previous-commit>
docker compose up -d --build
```

### A migration has run

**Do not simply redeploy the old code.** The old code against the new schema is
a different broken state from the one you started in, and usually a worse one.

1. Decide whether the migration is reversible:

   ```bash
   docker compose exec api python manage.py showmigrations
   docker compose exec api python manage.py migrate <app> <previous_migration> --plan
   ```

2. If it reverses cleanly, reverse it and then roll the code back:

   ```bash
   docker compose exec api python manage.py migrate <app> <previous_migration>
   git checkout <previous-commit> && docker compose up -d --build
   ```

3. If it does not reverse — a dropped column, a destructive data migration —
   restore from backup. This loses everything written since the dump, so it is
   the last option, not the first:

   ```bash
   docker compose down
   docker compose up -d db
   docker compose exec -T db dropdb -U genmars --force genmars_portal
   docker compose exec -T db createdb -U genmars genmars_portal
   docker compose exec -T db pg_restore -U genmars -d genmars_portal \
       --no-owner --exit-on-error /backups/portal-<stamp>.dump
   git checkout <previous-commit>
   docker compose up -d --build
   ```

### Caddy only

```bash
sudo rm /etc/caddy/conf.d/genmars-portal.caddy
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

The portal goes dark; `genmars.co.ke` and the client site are untouched.

---

## When something breaks

```bash
docker compose ps                      # who is unhealthy
docker compose logs --tail=100 api
docker compose logs --tail=100 web
journalctl -u caddy -n 50 --no-pager
```

Un-caught 500s also email `info@genmars.co.ke` (`AdminEmailHandler`). If the
portal is broken and no mail arrived, suspect the mail path itself:

```bash
docker compose logs api | grep -i resend
```

`resend accepted message id=…` means it left here and the rest is Resend's
dashboard. `resend rejected message: HTTP 403 …` is almost always an unverified
sending domain. `resend unreachable` is egress, not configuration.

The id is safe to paste into a ticket — it is an opaque handle, not content.
Nothing in that log line contains a verification code, and nothing should ever
be added to it that does.

---

## Restore-test log

Tier 1 asks for a *tested* restore, and "tested" means recently, not once.
Record each successful run:

| Date | Dump | Result | By |
|---|---|---|---|
| 2026-09-01 | `portal-20260901-125232.dump` | Passed — 9 tables, data present | First deploy |
