# Security audit — 9 September 2026

A review of the running system and the four repositories behind it, done in one
sitting on 2026-09-09. Every finding below was verified against production or
against the code as deployed; nothing here is inferred from a version number
alone or carried over from an earlier document.

**Scope.** `gen-portal` (Django API + client portal), `internals-tm`
(operations), `gen-website` (marketing), the Hetzner host, the Caddy edge, TLS,
backups and the dependency trees of all four. `business-os` was **not**
reviewed — its contents are deliberately unread.

**Not covered, and it matters.** No penetration testing was performed: no
credential guessing was attempted against the live admin, no payload was sent
to any endpoint, and no client data was read. Findings 2 and 3 are reasoned
from code and configuration, which is enough to act on and is not the same as
demonstrated exploitation. There was also no review of M-Pesa's live behaviour,
because no STK push has ever been fired.

---

## Summary

| # | Finding | Severity | Fixable by us |
|---|---|---|---|
| 1 | Django 5.1 has been end-of-life since 2025-12-03 | **High** | Yes — 5.2 LTS |
| 2 | The Django admin login bypasses the lockout and every throttle | **High** | Yes |
| 3 | `ops.genmars.co.ke` ships almost no security headers | Medium-high | Yes, today |
| 4 | The lockfile the build claims to use does not exist | Medium | Yes |
| 5 | Four nightly backups are stranded on the one disk | Medium | Yes, two commands |
| 6 | The API container has a writable root filesystem | Low-medium | Yes |
| 7 | `postcss` HIGH advisory in all three frontends | Low | Yes, one command |

Nothing found is being actively exploited as far as anything visible shows, and
nothing found exposes client data to the internet today. Findings 1 and 2 are
the two that would matter most in the hands of somebody trying.

---

## 1. Django 5.1 is out of security support — HIGH

`requirements.txt` pins `Django==5.1.*`. The installed version is **5.1.15**,
which is the *final* release of that series.

| Series | Released | End of life | LTS |
|---|---|---|---|
| **5.1 (ours)** | 2024-08-07 | **2025-12-03** | no |
| 5.2 | 2025-04-02 | 2028-04-30 | **yes** |
| 6.0 | 2025-12-03 | 2027-04-30 | no |
| 6.1 | 2026-08-05 | 2027-12-31 | no |

The framework the entire company runs on has had **no security patches for
nine months**. Any Django vulnerability published since December 2025 is
present in production and will stay there: there is no upstream fix to take,
and the `5.1.*` pin means `pip install` can never move off it — it resolves to
5.1.15 forever.

This is not a theoretical exposure. Django's own security releases are how
authentication, SQL-parameterisation and file-handling defects reach
applications, and this one stopped receiving them before the portal had its
first client.

**What to do.** Move to **5.2 LTS**, supported until April 2028. It is one
minor version, the release notes for 5.2 are short, and the test suite is 867
tests deep — this is an afternoon with a real safety net, not a migration.
Do **not** go to 6.1: it buys fifteen months where the LTS buys nineteen, and
costs two majors of churn.

Check the Python pin at the same time. `backend/Dockerfile` and CI pin 3.13
because Django 5.1 breaks on 3.14; whether 5.2 changes that is worth
establishing during the upgrade rather than assumed either way.

## 2. The admin login bypasses the lockout and every throttle — HIGH

`https://api.genmars.co.ke/admin/login/` returns 200 to the internet.

`accounts/identity.py` enforces a five-attempt lockout with a fifteen-minute
expiry, and DRF throttles `auth_sign_in` at 10/min. **Neither applies here.**
Django's admin login uses `AuthenticationForm`, which calls
`django.contrib.auth.authenticate` and the default `ModelBackend`. That path
never reads `User.locked_until`, and DRF's throttling is a DRF concern — an
admin view is not a DRF view.

So the portal's sign-in endpoint is hardened and the admin's is not, against
the same password, on the same accounts. The accounts reachable this way are
staff and superuser accounts, which read across **every** organisation.

Argon2 makes each attempt expensive, which bounds the rate but does not stop
the attack, and it is the only thing in the way.

Compounding it: **`django-ratelimit==4.1.*` is a declared dependency and is
imported nowhere in the codebase.** The tool for this job is already installed,
already paid for under Charter 03 §I, and unused.

**What to do**, in increasing order of effort:

1. Move the admin off `/admin/` to an unguessable path. Cheap, and it removes
   the whole class of untargeted scanning — but it is obscurity, not a control,
   and should not be the only step.
2. Rate-limit the admin login at the edge. Caddy sees every request and needs
   no application change.
3. Apply the lockout where it belongs: a custom `AuthenticationBackend`, or a
   `ModelBackend` subclass, that refuses while `locked_until` is in the future.
   That makes one rule true everywhere instead of true in the module that
   happens to be well written — which is the same argument the identity
   boundary already makes.

## 3. Operations ships almost no security headers — MEDIUM-HIGH

Measured on 2026-09-09:

| Header | genmars.co.ke | app. | api. | **ops.** |
|---|---|---|---|---|
| Strict-Transport-Security | yes | yes | yes | yes |
| X-Frame-Options | yes | yes | yes | **no** |
| X-Content-Type-Options | yes | yes | yes | **no** |
| Referrer-Policy | yes | yes | yes | **no** |
| Cross-Origin-Opener-Policy | yes | — | yes | **no** |
| Permissions-Policy | yes | — | — | **no** |
| Content-Security-Policy | yes | — | — | **no** |

`/etc/caddy/conf.d/ops.caddy` sets `Strict-Transport-Security` and
`X-Robots-Tag` and nothing else. The surface with the weakest headers is the
one that reads across every organisation in the company.

The one that matters most is the missing frame protection. With no
`X-Frame-Options` and no `frame-ancestors`, any page anywhere can load
`ops.genmars.co.ke` in an invisible iframe over its own content and collect
clicks from a signed-in founder — and operations now carries controls where a
single click matters, including the sibling sign-on Continue button and the
approval actions.

**What to do.** Add the same header block `app.` and `api.` already carry.
`Content-Security-Policy` should follow separately: Next.js emits inline
scripts for hydration, so a CSP written without testing takes the dashboard
down, and it deserves its own change with a rollback path rather than being
bundled in with headers that cannot break anything.

## 4. There is no lockfile — MEDIUM

`backend/requirements.txt` opens with:

> `Pinned to majors; lockfile is requirements.lock.txt.`

**`requirements.lock.txt` does not exist.** The image builds from
`requirements.txt` alone, which pins to majors: `Django==5.1.*`,
`psycopg[binary]==3.2.*`, `celery==5.4.*`.

Three consequences, none of them theoretical:

- **The image is not reproducible.** Two builds of the same commit a week apart
  can install different code. The SHA-tagged artefact is the only thing that
  pins anything, and it pins the *result*, not the inputs.
- **Rollback cannot restore a dependency set.** If a new patch release breaks
  production, `git revert` does not undo it.
- **A compromised release is installed silently.** No hashes, no pinned
  versions: a malicious `psycopg` 3.2.x published tonight is in tomorrow's
  image with nothing to notice it. This is the shape most supply-chain
  compromises actually take.

**What to do.** Generate it — `pip freeze` into `requirements.lock.txt`, or
`pip-compile --generate-hashes` if hash verification is wanted — install from
it in the Dockerfile, and let CI fail when it drifts. Or delete the sentence.
The sentence being there while the file is not is the worst of the three
states, because it stops anyone looking.

## 5. Four nightly backups are stranded on one disk — MEDIUM

```
-rw------- 1 root  root  portal-20260906-021658.dump.gpg
-rw------- 1 root  root  portal-20260907-021558.dump.gpg
-rw------- 1 root  root  portal-20260908-021510.dump.gpg
-rw------- 1 root  root  portal-20260909-021658.dump.gpg
-rw------- 1 edwin edwin portal-20260909-100727.dump.gpg
-rw------- 1 edwin edwin portal-20260909-134616.dump.gpg
```

The nightly timer runs as root and writes `0600 root:root`;
`scripts/pull-backups.sh` runs as `edwin` and cannot read them. Only the copies
made by hand today can leave the machine. **Every automated backup since
6 September exists in exactly one place**, and that place is the machine the
backups are for.

The encryption is sound and the key is off-box, which is the harder half.
This is the easy half, and it has been outstanding for four days.

**What to do.** Two commands, both offered previously and neither authorised:
`chown` what is stuck, and a `chown --reference` in `backup.sh` when it runs as
root so it stops recurring.

## 6. The API container has a writable root filesystem — LOW-MEDIUM

```
genmars-portal-api  user=10002  readonly=false  caps=[ALL dropped]
genmars-ops         user=10003  readonly=true   caps=[ALL dropped]
```

Operations proves the pattern works. On the API container an attacker who
achieves execution can write to the image filesystem and persist across a
restart; with `read_only: true` and a `tmpfs` for `/tmp`, they cannot.

Both already run as a non-root user with every capability dropped, which is the
larger part of the work and is done.

## 7. `postcss` advisory in all three frontends — LOW

`npm audit --omit=dev` reports one HIGH (`postcss`, XSS via unescaped
`</style>` in stringify output; arbitrary file read) and one moderate (`next`,
via the same) in each of the three frontends. A fix is available in all three.

Reachability is poor: postcss processes CSS we author, at build time, and
there is no path by which an attacker supplies stylesheet input. It is a
maintenance item rather than an exposure — but it is one command, and a
permanently non-empty `npm audit` is how the next advisory gets skimmed past.

---

## What was verified as sound

Stated because an audit that lists only faults gives no sense of the baseline,
and because several of these are the reason the findings above are not worse.

- **TLS everywhere**, Let's Encrypt, auto-renewing, valid to 30 November 2026,
  HTTP/2, HSTS on all four hosts. `www` and `http` redirect permanently.
- **Argon2** password hashing, asserted by a test that fails if the production
  hasher is ever swapped for the fast test one.
- **Session and CSRF cookies**: `HttpOnly`, `Secure` outside DEBUG, `SameSite=Lax`,
  distinct names. `SESSION_COOKIE_DOMAIN` is unset, which is what keeps a
  portal session from being replayable against operations — the codebase warns
  against setting it in two places, and both warnings are still there.
- **DEBUG is off in production** — a 404 returns a plain page with no stack
  trace, no settings and no version.
- **Tenant isolation lives in one file** (`portal/selectors.py`) and guessable
  references return 404 rather than 403, so the API cannot be used to confirm a
  record exists.
- **Uniform authentication failures** — the same message whether or not an
  address exists, so sign-in cannot be used to enumerate accounts.
- **Throttling on every auth endpoint**, including the sibling sign-on added
  this week: 10/min sign-in, 5/hour sign-up, 6/hour code requests (each of
  which sends an email), 30/min sign-on, 60/min token exchange, with a separate
  per-client scope.
- **The sibling sign-on invariants hold** as deployed: grants are hashed,
  single-use, 90 seconds, bound to the address they were issued for; redirect
  addresses are matched whole rather than by prefix; a presented-but-wrong code
  is burned; every `/token` refusal returns the identical message; and
  registered addresses must be live `https`.
- **The identity boundary is now enforced** rather than asserted —
  `scripts/check_identity_boundary.py` runs in CI as of today, and it found two
  real violations on the day it was written.
- **Containers** run as non-root with every Linux capability dropped.
- **`/opt/gen-portal/.env` is `0600 edwin:edwin`.**
- **Backups are GPG-encrypted** to a key held only on the founder's laptop, and
  the weekly automated restore test passes.
- **`security.txt`** is published, valid to September 2027, with a real mailbox.
- **The marketing site makes no third-party requests** — no analytics, no CDN,
  no font host — which is what makes the privacy policy's claim on that point
  true rather than aspirational.

---

## The pattern worth naming

Three controls were found this week that were **documented as existing and did
not exist**:

1. `scripts/check_identity_boundary.py` — cited in `identity.py` as enforcing
   the boundary in CI. Never written. Two violations had accumulated behind it.
2. The monthly restore drill — documented, and impossible to run: the `gpg`
   call carried `--batch`, so it could only ever fail, and it failed by
   announcing the encryption key was lost.
3. `requirements.lock.txt` — named in `requirements.txt` as the lockfile. Never
   created (finding 4, still open).

Each was written in good faith as a description of what *should* be true. The
failure mode is identical in all three: **a documented control that nothing
tests reads exactly like a working one**, and it stops the next person looking.
The first two were fixed on 2026-09-09.

A rule worth adopting: a control gets a test, or it comes out of the
documentation. There is no third state worth keeping.

---

## Recommended order

1. **Django 5.2 LTS** (finding 1) — the largest exposure, and the clock has
   been running nine months.
2. **The admin login** (finding 2) — an edge rate-limit is an hour's work and
   removes most of the risk while the backend fix is written.
3. **The ops headers** (finding 3) — everything except CSP is safe to ship
   immediately.
4. **The backup ownership** (finding 5) — two commands, four days overdue.
5. **The lockfile** (finding 4), then the read-only filesystem (6) and
   `npm audit fix` (7).

## Related

- `scripts/check_identity_boundary.py` — the boundary, now enforced
- `docs/DEPLOYMENT.md` — how the production changes above get made
- `gen-website/docs/SEO.md`, `docs/SEARCH-CONSOLE.md` — the public surface
