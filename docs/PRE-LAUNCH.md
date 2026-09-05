# Pre-launch status

**Reconciled 2026-09-05.** Read the next section first: the condition this
document was written to warn about has now happened.

---

# The portal is in production use, with real client data

On 2026-09-04 the portal stopped being a deployed system and became a running
one. In a single day it took on a real client (Clips Serenity Spa), a client
login, contact details, a domain under a client's own name, an uploaded file,
an order, and two invoices — one of them raised, sent and paid.

That matters because two documents in this repo name **"one real client
account"** as the exact trigger for work that has not been done:

- `src/app/privacy/page.tsx` in `gen-website`, in its own header comment:
  *"once the portal holds one real client account, this policy has to be
  rewritten against the data processing record rather than patched."*
- `PORTAL-INTEGRATION.md` §5.2, still open.

Neither was a prediction. Both were a tripwire, and it has been stood on.

**Nothing here says stop.** The work done yesterday was real work for a real
client and the system recorded it correctly. What it says is that item 6 below
moved from *approaching* to *overdue*, and it is the founder's to close, not
engineering's (Charter 02 §II — public statements are the founder's).

The one thing keeping this contained: `genmars.co.ke/robots.txt` is
`Disallow: /`, so none of the published policy text is indexed. That is a
mitigation, not a fix. The page is still reachable by anyone given the link,
and the client was given a link to the system it describes wrongly.

---

# Tier 1 status — Charter 03 §IV

| # | Item | State |
|---|---|---|
| 1 | Least-privilege roles, enforced in querysets | **Done** |
| 2 | TLS everywhere; Secure / HttpOnly / SameSite cookies | **Done — TLS confirmed live** |
| 3 | Automated Postgres backup with a **tested** restore | **Partial — restore still unproven** |
| 4 | Error monitoring that reaches a human | **Done — untested in production** |
| 5 | Documented deploy and rollback | **Done — exercised repeatedly** |
| 6 | Privacy policy and terms of service published | **Drafted — awaiting advocate** |

### 1 — Least privilege

`portal/selectors.py` is the single choke point for every client-facing query;
`is_staff` grants nothing on that path. `portal/tests/test_isolation.py` proves
Organisation A cannot read Organisation B, and `OrderDetailView` returns 404
rather than 403 so a reference cannot be confirmed by probing.

Now carrying real weight rather than test weight: there are three client
organisations in production and one of them has a login.

### 2 — TLS and cookies

Confirmed against production on 2026-09-05: `api.genmars.co.ke` answers over
HTTP/2 with `strict-transport-security: max-age=31536000`. Caddy terminates TLS
for all four hostnames.

`SESSION_COOKIE_SECURE` / `CSRF_COOKIE_SECURE` follow `not DEBUG`; `HttpOnly`
and `SameSite=Lax` are set; `manage.py check --deploy` is clean with two
warnings silenced *because Caddy handles them* (documented in `settings.py`).

Still worth one deliberate check: the cookie **attributes** have been confirmed
from settings and from an unauthenticated response, not read off a `Set-Cookie`
header on a real sign-in. Sign in once with devtools open and read the actual
header. Settings claiming a cookie is `Secure` is not the same as a browser
receiving one.

### 3 — Backups — the honest gap, narrower than it was

What is now genuinely working:

- The nightly dump **runs and produces real data.** `genmars-portal-backup.timer`
  last fired 2026-09-04 02:16 UTC, exit 0.
- Dumps are real Postgres dumps of a real database, not a rehearsal against
  SQLite.
- Encrypted copies are produced for `413CB8DF5FECF5F4` and the script refuses
  to keep one that is empty or addressed to the wrong key.

Three things are still not true, in descending order of how much they matter:

- **The restore test has never executed.** `genmars-portal-restore-test.timer`
  is installed and scheduled — first run 2026-09-06 03:36 UTC — and its journal
  is empty. A unit that has never run reports `Result=success`, which is the
  most misleading output available; do not read it as a pass. Until it runs
  green once, a dump is a file we believe in, not a backup.
- **The newest encrypted copy predates the real client data.** The last `.gpg`
  is `portal-20260904-021658.dump.gpg`, from 02:16 — and the first real client
  was created at 09:17 the same morning. Every dump taken after that point was
  taken by hand, without `BACKUP_RECIPIENT` in the environment, so it is
  unencrypted and sitting on the same disk as the database it came from.
  Tonight's 02:19 run closes this on its own. To close it now:
  `sudo systemctl start genmars-portal-backup.service`.
- **Moving copies off the box is manual.** There is no cron and no user timer
  doing the pull; `~/Genmars/backups-offbox/` is filled by hand, and the files
  there are the *unencrypted* dumps. A destination with an append-only
  credential is still an open decision — a backup an attacker can delete is a
  backup an attacker will delete.

Until the restore test runs green, **do not describe the portal as backed up.**

### 4 — Error monitoring

Un-caught 500s email `info@genmars.co.ke` via `AdminEmailHandler`.
`accounts/tests/test_monitoring.py` proves a 500 actually produces a message to
the configured address, and that it carries **no HTML traceback** — the HTML
version embeds local variables, which here means session keys, email addresses
and submitted form values, mailed in plain text through a relay.

Untested in production only in the sense that no 500 has occurred to test it
with, which is the good version of untested. It has now had a full day of real
use to fire on and stayed quiet.

Sentry was considered and deliberately not added: Charter 03 §I asks that a new
tool enter the stack only when what we already run cannot do the job, and
routing exception payloads to a third-party processor is a decision to defer
while the controller/processor position with the ODPC is open (Charter 03 §V).

### 5 — Deploy and rollback

`docs/DEPLOYMENT.md`, including the case that matters: rolling back *after a
migration has run*, where redeploying the old code against the new schema is a
different broken state and usually a worse one.

No longer theoretical. The runbook was followed for every deploy on 2026-09-04,
including two that carried migrations.

### 6 — Privacy policy and terms — DRAFTED 2026-09-05, awaiting advocate

The documents now exist. What is left is a signature, not a keyboard.

**Written on 2026-09-05, all four verified against the running system:**

| Document | Where | Covers |
|---|---|---|
| Privacy policy v1.0 | `genmars.co.ke/privacy/` | Two parts: the website, and the portal |
| Terms of service v1.0 | `genmars.co.ke/terms/` | Website use and portal accounts |
| Terms of Business v1.0 | `05-policies/` | Paid engagements |
| Data Processing Agreement v1.0 | `05-policies/` | Personal data processed for a client |

`05-policies/README.md` carries the advocate brief — what to send, and the
eight points where a non-lawyer drafting from the system is most likely to have
got the law wrong rather than the facts.

**What this closed:**

- The privacy policy no longer describes a company that holds no data. Part two
  documents the portal: what is held, why, for how long, and who else sees it.
- `/terms/` is no longer a placeholder. This was worse than an open item: the
  sign-up screen has said *"by creating an account you agree to the terms of
  service"* since it shipped, linking to a page that said the document was not
  published. Accounts were created against terms that did not exist.
- The portal now links both documents from its own footer, absolute rather than
  relative — a relative href resolves to `app.genmars.co.ke/privacy`, which
  does not exist.
- The sentences pointing at "the portal's own privacy policy" now point at
  something. It is folded into the marketing-site document rather than
  published separately: one controller, one policy, and it stays readable when
  the portal is down.

**Two things the drafting found that nobody had written down:**

1. **Client personal data is stored in Falkenstein, Germany** (Hetzner
   `fsn1-dc14`). Sections 48–50 of the Data Protection Act, 2019 govern
   transfer outside Kenya, and no version of any Genmars document had ever
   mentioned it. It is now section 12 of the privacy policy and clause 7 of the
   DPA, both stated at the front rather than buried in an annex.

2. **The internal contact log is personal data.** `ContactLogEntry` holds frank
   notes about clients, including opinions, that clients never see. Opinions
   about an identifiable person are reachable by a subject access request.
   Section 6 of the privacy policy says so plainly.

**Still open, and none of it is engineering's:**

- **The advocate engagement.** Every document carries a visible "not reviewed"
  notice and both pages keep `noindex`; `robots.txt` stays `Disallow: /`.
- **`privacy@` and `security@` deliverability.** Both are named as the address
  for statutory requests and neither has been tested from outside the domain.
  An address that bounces is worse than none.
- **The ODPC controller/processor position** (Charter 03 §V). The published
  documents deliberately make no claim in either direction. Do not add one
  until it is settled.
- **KRA PIN.** Terms of Business clause 7.8 cannot be finalised without it.

**The advocate engagement is still the only thing on the critical path with a
client's data on the wrong side of it — but it is now a review, not a drafting
job.**

---

## Standing constraints

### The Python base image stays pinned to 3.13

`backend/Dockerfile` builds and runs on `python:3.13-slim`, pinned, in both
stages. Keep it that way — never `python:3-slim`, never `python:latest`.

Django 5.1 supports Python 3.10–3.13. On 3.14 the admin raises
`AttributeError: 'super' object has no attribute 'dicts'` in
`django/template/context.py` on any changelist render. A floating tag would
introduce that on whichever rebuild happened to pull a newer interpreter, and
it would pass the API smoke tests, because nothing outside the admin templates
touches that code path.

Revisit when Django moves to a release that supports 3.14 (6.0 does).

### CSRF_TRUSTED_ORIGINS must stay set

`backend/.env` sets `CSRF_TRUSTED_ORIGINS=https://app.genmars.co.ke`.
`config/settings.py` refuses to boot without it when `DEBUG=False`, on purpose.
Left empty it fails in the most misleading way available: sign-in still works,
because DRF's `SessionAuthentication` only enforces CSRF once a request is
authenticated and an anonymous POST never reaches that check. A smoke test that
signed in successfully would pass while every authenticated write returned 403.

### Mail configuration is boot-critical

With `DEBUG=False`, Django refuses to boot without a working mail
configuration — an unsendable `EMAIL_BACKEND` or an empty `EMAIL_HOST_PASSWORD`
both raise `ImproperlyConfigured`. That was a warning until 2026-08-28 and it
was too weak: the development default is the *file* backend, so simply not
setting `EMAIL_BACKEND` in production made sign-up, verification, password
reset and error alerts all report success while delivering nothing, with no
trace in any log.

Zoho is live and verified (SPF, DKIM on selector `zmail`, DMARC `p=none`) — see
`09-communication/README.md`.

---

## Open calls that are not tasks

These need a decision, not an implementation. They are listed so they are not
mistaken for work that someone forgot.

- **Internal TLS between the API and Postgres.** Both run in the same Docker
  network on one host. Founder's call.
- **The off-box backup destination**, and an append-only credential for it.
- **The ODPC controller/processor position** (Charter 03 §V), which gates the
  policy rewrite above.

---

## Business data still missing

Not launch blockers, but each one is visibly absent on a document a client
reads:

- **KRA PIN is empty**, and `GM-INV-2026-0004` has already been issued and paid
  without one. The field's own help text says it is required on a Kenyan tax
  invoice; the document omits the line rather than printing a blank, so nothing
  looks broken — it is just missing from an invoice that has already gone out.
- `mpesa_account_hint` is empty. This one is safe: `account_reference()` falls
  back to the invoice number, which is the right default anyway.
- `bank_details` and `terms` are empty. The invoice degrades honestly — with a
  paybill set it shows the paybill, and with nothing set it tells the client to
  ask their contact rather than inventing a number.

Enter these at `ops.genmars.co.ke/company`.

---

## The one-line summary

The portal is **deployed, running, and carrying a real client's data.**
Everything an engineer can build for Tier 1 is built. What remains is a legal
engagement that is now overdue, and one restore test that has never run.
