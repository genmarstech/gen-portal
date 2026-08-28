
## Environment — CSRF_TRUSTED_ORIGINS

`backend/.env` must set:

```
CSRF_TRUSTED_ORIGINS=https://app.genmars.co.ke
```

`config/settings.py` refuses to boot without it when `DEBUG=False`, on purpose.
Left empty it fails in the most misleading way available: sign-in still works,
because DRF's `SessionAuthentication` only enforces CSRF once a request is
authenticated and an anonymous POST never reaches that check. A smoke test that
signs in successfully would pass while every authenticated write — change
password, and everything added after it — returned 403.

`backend/.env.example` documents the full set.

## Pin the Python base image to 3.13

`deploy/` is empty — the portal has no Dockerfile yet. When one is written, the
base image must be **`python:3.13-slim`**, pinned, never `python:3-slim` or
`python:latest`.

Django 5.1 supports Python 3.10–3.13. On 3.14 the admin raises
`AttributeError: 'super' object has no attribute 'dicts'` in
`django/template/context.py` on any changelist render, which removes the only
way staff create orders in v1. A floating tag would introduce that silently on
whichever rebuild happens to pull a newer interpreter — and it would pass the
API smoke tests, because nothing outside the admin templates touches that code
path.

Revisit when Django moves to a release that supports 3.14 (6.0 does).

---

# Tier 1 status — Charter 03 §IV

Six items. Four are done, one is partial, one cannot be closed by engineering.

| # | Item | State |
|---|---|---|
| 1 | Least-privilege roles, enforced in querysets | **Done** |
| 2 | TLS everywhere; Secure / HttpOnly / SameSite cookies | **Done — untested in production** |
| 3 | Automated Postgres backup with a **tested** restore | **Partial** |
| 4 | Error monitoring that reaches a human | **Done — untested in production** |
| 5 | Documented deploy and rollback | **Done** |
| 6 | Privacy policy and terms of service published | **BLOCKED** |

### 1 — Least privilege

`portal/selectors.py` is the single choke point for every client-facing query;
`is_staff` grants nothing on that path. `portal/tests/test_isolation.py` proves
Organisation A cannot read Organisation B, and `OrderDetailView` returns 404
rather than 403 so a reference cannot be confirmed by probing.

### 2 — TLS and cookies

`SESSION_COOKIE_SECURE` / `CSRF_COOKIE_SECURE` follow `not DEBUG`; `HttpOnly`
and `SameSite=Lax` are set; `manage.py check --deploy` is clean with two
warnings silenced *because Caddy handles them* (documented in `settings.py`).

**Not yet verified in a real browser against real TLS**, because nothing is
deployed. Step 8 of `docs/DEPLOYMENT.md` is where that gets confirmed. Settings
claiming a cookie is `Secure` is not the same as a browser receiving one.

### 3 — Backups — PARTIAL, and this is the honest gap

Written and syntax-checked: `scripts/backup.sh`, `scripts/restore-test.sh`, and
systemd units for a nightly dump plus a **weekly restore test**. The restore
test asserts the tables exist *and* that rows came back — a schema-only restore
that "succeeds" into an empty database is the exact failure it exists to catch.

Two things are genuinely not done:

- **Never executed.** There is no Postgres to dump — the portal runs on SQLite
  in development. The scripts are unproven until step 7 of the deploy runbook.
- **No off-box copy.** Every dump would sit on the same disk as the database it
  came from, so one failed volume loses both. This needs a destination decision
  and an append-only credential; a backup an attacker can delete is a backup an
  attacker will delete.

Until both are cleared, **do not describe the portal as backed up.**

### 4 — Error monitoring

Un-caught 500s email `info@genmars.co.ke` via `AdminEmailHandler`.
`accounts/tests/test_monitoring.py` proves a 500 actually produces a message to
the configured address, and that it carries **no HTML traceback** — the HTML
version embeds local variables, which here means session keys, email addresses
and submitted form values, mailed in plain text through a relay.

Sentry was considered and deliberately not added: Charter 03 §I asks that a new
tool enter the stack only when what we already run cannot do the job, and
routing exception payloads to a third-party processor is a decision to defer
while the controller/processor position with the ODPC is open (Charter 03 §V).

With `DEBUG=False` and no `EMAIL_HOST_PASSWORD`, Django warns at boot that the
channel is dead. Deploying without that password means **this item is not met**,
whatever this document says.

### 5 — Deploy and rollback

`docs/DEPLOYMENT.md`, including the case that matters: rolling back *after a
migration has run*, where redeploying the old code against the new schema is a
different broken state and usually a worse one.

### 6 — Privacy policy and terms — BLOCKED, and not by engineering

This one cannot be closed by writing code, and it is what stands between the
portal and real client data:

- `/terms/` on the marketing site is still a placeholder. The Policy Pack
  requires terms acceptance recorded at signup for any product with accounts,
  and there is currently nothing to link to.
- The published privacy policy states "No accounts. There is nothing to sign up
  for" and "no personal data at rest". Both were true; both are false the day
  this ships. It needs rewriting against the data processing record, not
  patching.
- The controller/processor position with the ODPC (Charter 03 §V) is still open,
  and the answer changes the policy's framing.
- `privacy@genmars.co.ke` is named in the published policy and does not exist.

The advocate engagement the Policy Pack asks for is now on this project's
critical path.

---

## The one-line summary

The portal is **deployable**, and it is **not launchable**. Everything an
engineer can build for Tier 1 is built; what remains is a legal engagement, a
first deploy, and proving the backups on real data.
