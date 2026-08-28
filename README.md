# gen-portal

Client portal for **Genmars Tech Limited** — `app.genmars.co.ke`.

Clients sign in to see where their project actually is: scope and exclusions,
the weekly progress note, milestones and payment status. Charter 05 promises all
of it; this is the surface that makes it visible rather than asserted.

> **Deployable, not launchable.** [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) is
> the runbook for `app.genmars.co.ke` + `api.genmars.co.ke`. It must not hold
> real client data yet: the privacy policy and terms still block it, and the
> backups have never been run against a real database. See
> [`docs/PRE-LAUNCH.md`](docs/PRE-LAUNCH.md).

---

## Requirements

**Python 3.13.** Not 3.14 — Django 5.1 supports 3.10–3.13, and on 3.14 the
admin dies with `AttributeError: 'super' object has no attribute 'dicts'`
whenever it copies a template context. That takes out every changelist,
including the one where staff create orders, which is the whole staff workflow
in v1. The application code is not on that stack; the version pairing is the
bug.

```bash
# Fresh checkout
cd backend
"$LOCALAPPDATA/Programs/Python/Python313/python.exe" -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements-dev.txt   # includes runtime deps
```

`requirements.txt` is the runtime; `requirements-dev.txt` adds pytest and pulls
the runtime in. Install the dev file locally and in CI — the runtime file alone
cannot run the tests.

---

## Status

| Step | State |
|---|---|
| 1. Skeleton — Django, custom user, models | **Done** |
| 2. Identity boundary + auth API | **Done** — 96 tests passing |
| 3. Auth UI (Next.js) | **Done** — 5 screens, both themes, both breakpoints |
| 3b. Onboarding | **Done** — 2 steps, produces an Enquiry, never an Order |
| 4. Dashboard | **Done** — API, order list, detail, empty state |
| 5. Export + account settings | **Done** — self-serve JSON export, change password |
| 6. Tier 1 completion | **4 of 6 done**, backups unproven, policy blocked — [docs/PRE-LAUNCH.md](docs/PRE-LAUNCH.md) |

```bash
cd backend && .venv/Scripts/python.exe -m pytest   # 96 tests
cd frontend && npm run verify                      # theme check + types + build
cd frontend && npm run dev                         # http://localhost:3010
```

Run both: Django on `:8010`, Next on `:3010`. Next's rewrite proxies `/api/*`
to Django, so in development the app sees one origin exactly as it will behind
Caddy in production.

```bash
cd backend  && .venv/Scripts/python.exe manage.py runserver 127.0.0.1:8010
cd frontend && npm run dev
```

Verification codes go to **files** in `backend/sent-emails/` in development, not
the console — read the newest with
`bash scripts/latest-code.sh`. Server stdout is buffered on Windows, so console output is
unreadable exactly when you need to read a code.

To work on the UI without the backend, uncomment `NEXT_PUBLIC_AUTH_MOCK=1` in
`frontend/.env.local`.

### Two things that will bite you

**Never run `next build` while `next dev` is running.** They share `.next`, the
build replaces the chunks the dev server has open, and the page dies with
`Cannot find module './0.js'`. `npm run verify` now cleans `.next` first, but
stop the dev server before running it.

**Django rotates the CSRF token when a session starts.** A token read once and
cached goes stale the moment someone signs in, and every later POST returns 403.
`api.ts` reads `document.cookie` fresh on every request for exactly this reason
— do not "optimise" that into a module-level constant.

---

## Why it is a separate application

`gen-website` is a static export. Its defining security property is that the
runtime image contains no Node and no application code — nothing to patch at
3am. Auth needs a server, which would destroy that, and it would put client
personal data on the marketing domain.

So the portal is its own repo, its own container, its own subdomain. The
marketing site stays exactly as it is.

```
app.genmars.co.ke
        │  host Caddy (TLS)
        ├── /api/*  ──▶ Django + DRF   127.0.0.1:8010
        └── /*      ──▶ Next.js        127.0.0.1:3010
```

One origin, two upstreams — so session cookies work without CORS. Ports 3000 and
8085 are taken on that host by gen-website and Clips Serenity Spa.

---

## The identity boundary

**`backend/accounts/identity.py` is the most important file here.**

Every authentication operation goes through it. Nothing else calls
`create_user`, `check_password`, or `django.contrib.auth.authenticate`.

That is because **AuthGate replaces this**. When it lands, `identity.py` becomes
an HTTP client for AuthGate and nothing else changes. That is a day's work if the
boundary holds, and a rewrite if auth logic has been allowed to spread.

Session cookies, not JWT in localStorage — a token readable by JavaScript is a
token any XSS can exfiltrate.

---

## Two bugs the tests caught

Both were mine, both in code that looked correct, and both would have shipped
silently. Worth recording because the second is a pattern, not a typo.

### The brute-force cap did nothing

`redeem_code` was decorated `@transaction.atomic` **and raised on failure**.
Raising inside an atomic block rolls the block back — so every increment of
`attempts` was discarded on the way out. The counter sat at zero permanently and
a six-digit code could be guessed without limit.

Nothing errored. The cap was simply never reached, which is worse than having no
cap, because the cap was believed.

The fix: do the work inside an explicit `atomic()` block, let it **commit**, and
raise afterwards.

### The same trap one level up

`complete_password_reset` wrapped the whole flow in `@transaction.atomic` and
called `redeem_code`. Nested atomics are savepoints, so an outer rollback undoes
the inner work too — the same counter, discarded the same way, on the path where
the consequence is account takeover rather than a nuisance.

**The rule, now written into both docstrings:** if a function records a failed
attempt and then raises, it must not be wrapped in a transaction that the raise
will roll back. Neither may its callers.

Both are pinned by `test_failed_attempts_persist_across_calls` and
`test_failed_reset_attempts_also_persist`.

---

## The screens

Five: sign in, sign up, verify, forgot, reset. Built from the design artboards
at their stated measurements — 52px controls, 2px radius, 11px labels at +180
tracking, 18px between fields.

**Desktop** is the split shell: a fixed 530px brand panel beside a 400px form.
**Mobile** drops the panel entirely — it is decoration, and decoration does not
get to compete for space on a phone.

### The brand panel artwork

Original, drawn from the Orbit G's own geometry: a wireframe body cropped by the
panel edge, the mark's −30° trajectory sweeping across it, halftone texture, and
fine technical annotation.

The direction came from three reference images now filed in
`06-brand/references/direction/`. They are third-party work — one carries
another company's logo — so they establish a language rather than appearing in
it. Nothing here is stock or borrowed, and the annotation is structural: no
counts, no metrics, nothing that reads as a claim (Charter 04 §IV).

The panel does **not** flip with the theme. A brand surface that inverted would
make light and dark read as two different products, so only the working column
beside it changes. `check-theme-tokens.mjs` now supports a declared opt-out for
exactly this case — the exception is argued in the file rather than silently
allowed.

### Where the design was changed, and why

The artboards draw inputs as static divs, which is right for a canvas and wrong
for an application. Every control here is real: labelled, keyboard-reachable,
announced. Three specifics:

- **The password reveal is a button**, not the design's static "Show" text, with
  `aria-pressed` carrying its state.
- **The disabled "Use phone instead" reads as disabled** — dashed border, muted.
  The design draws it live; shipping that invites a click that does nothing.
- **The code input is one field behind six cells.** Per-box inputs break paste,
  and pasting a code out of an email is how most people will use it.

---

## The auth API

`POST /api/auth/…` — `sign-in`, `sign-up`, `request-code`, `verify`, `forgot`,
`reset`, `change-password`, plus `GET/DELETE /api/auth/session`. Paths match
`frontend/src/lib/api.ts` exactly.

Views are thin by design: validate, call `identity`, translate the result. No
auth logic lives in them, which is what keeps the AuthGate swap to one module.

### Nothing distinguishes a registered address

Not the message, and **not the status code** — a different status is just as
good an enumeration oracle as a different message. So:

- sign-in returns the same 401 and body for unknown-email and wrong-password
- sign-up on an existing address returns the same 200 as a fresh one, and sends
  a verification code to the real owner rather than reporting the collision
- forgot and request-code always return `{"ok": true}`
- verify returns the same 400 for an unknown address as for a bad code

`test_unknown_email_is_indistinguishable_from_wrong_password` compares status
and body together.

### Rate limiting, in two layers

`identity.py` caps failures **per account** — five wrong passwords locks it for
fifteen minutes. That does nothing against one common password sprayed across a
thousand addresses: each account sees a single failure.

So the views add **per-IP** and **per-email** throttles. Sign-in 10/min, sign-up
5/hour, code requests 6/hour, and 8/hour per email address regardless of source.
Code endpoints are the strictest because every request sends mail — unthrottled,
that is a free way to use our domain to fill a stranger's inbox.

`NUM_PROXIES = 1` matters here: behind Caddy every request arrives from
127.0.0.1, and without it DRF would throttle the proxy rather than the caller,
letting one attacker lock out everybody.

---

## Isolation

`backend/portal/selectors.py` is the only place client-facing querysets are
built. Every read scopes through `Membership`; no view constructs its own
`Order` queryset.

One forgotten `.filter()` in one view is a confidentiality breach under Charter
05 §V, and it fails silently — nothing errors, another client's data simply
appears. A single choke point makes that structurally hard rather than merely
unlikely.

`is_staff` grants nothing here. Genmars staff use the Django admin; the
selectors answer only "what may this account see", always through membership.
There is a test for that too.

---

## Signup does not create an order

Anyone may register and complete onboarding, which produces an `Enquiry`. An
`Order` is created **by staff only**, after qualification and a signed SOW.

Charter 02 §I gives qualification to the commercial partners and the capacity
veto to the founder. A self-serve form that manufactured work would route around
both. `test_create_account_does_not_create_an_order` holds the line.

---

## Reused from `gen-website`

Copied rather than shared — a monorepo is more machinery than one engineer needs
today. **The duplication is deliberate and must be kept in step:** a token
change in one repo does not reach the other.

`globals.css` (186 tokens, the light/dark architecture, the `--accent-text`
contrast rule), `Brand.tsx`, `LoadingMark.tsx`, `theme.ts`, `ThemeToggle.tsx`,
`check-theme-tokens.mjs`, and the Dockerfile/compose hardening patterns.

The Dockerfile needs real changes: this image **does** contain Node and adds
Python, so gen-website's "no runtime dependencies" note stops being true and
must be rewritten rather than copied.
