FIRST, THE SHAPE OF IT
Four containers in /opt/gen-portal, on TWO networks. `data` carries Postgres
and Redis; `edge` carries HTTP between the frontends and Django. The api
container is the only thing on both — that is deliberate, and it means a
frontend cannot reach the database even if it wanted to.

  db     Postgres 16, data in the portal-db volume
  redis  throttle counters only, password-protected
  api    Django/gunicorn on 8010
  web    Next.js on 3020, proxies /api/* to the api container

Caddy on the HOST terminates TLS and routes app.genmars.co.ke and
api.genmars.co.ke. Config: /etc/caddy/conf.d/genmars-portal.caddy

WHAT TO CHECK FIRST, IN ORDER
  cd /opt/gen-portal
  docker compose ps                    # is anything unhealthy or restarting?
  curl -s https://api.genmars.co.ke/api/health
  docker compose logs api --tail 100
  df -h /                              # a full disk presents as everything broken

If the api is unhealthy but the db is fine, it is usually the api. If the db is
unhealthy, stop and read its logs before restarting anything — a restart loop
on a database is how a corrupt shutdown becomes a corrupt volume.

RESTART, LEAST DRASTIC FIRST
  docker compose restart api           # almost always enough
  docker compose up -d                 # recreates anything whose config changed
  systemctl reload caddy               # only if TLS or routing is the problem

Do NOT run `docker compose down -v`. The -v removes volumes, and the database
is a volume.

DEPLOY
  git pull --ff-only origin main
  ./scripts/backup.sh                  # BEFORE any migration
  docker compose build api web
  docker compose up -d
  docker compose exec api python manage.py migrate
  ./scripts/backup.sh                  # AFTER, because the newest dump now has
                                       # an older schema than the database

ROLLBACK
Code only: check out the previous commit and rebuild. With a migration: restore
the pre-migration dump — see docs/DEPLOYMENT.md, which has the exact
pg_restore. That loses everything written since, so it is a last resort and a
decision, not a reflex.

IF THE DATABASE IS GONE
  ls -1t backups/*.dump | head         # newest local, plaintext
  ~/genmars-backups/                   # encrypted off-box copies, on the
                                       # founder's machine, needing the private
                                       # GPG key that exists nowhere else
Restore procedure: docs/DEPLOYMENT.md. The restore test runs weekly and is the
reason anyone should believe these work.

THINGS THAT LOOK LIKE FAULTS AND ARE NOT
  · No alert email does not mean nothing is wrong. See GM-INC-2026-0001 — a
    suppressed address dropped 31 hours of alerts while the API answered 200.
    Operations shows a red banner if any address is suppressed; check there.
  · `gpg --list-packets` exits non-zero on this host. It has no private key.
    That is correct.
  · Three test_monitoring failures locally on Python 3.14 are a Django
    incompatibility, patched in conftest.py. CI runs 3.13.
