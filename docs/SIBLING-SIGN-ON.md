# Signing in to a sibling application

How `business-os` — and anything after it — lets people in using their Genmars
account, without keeping accounts of its own.

> **One set of company accounts.** A sibling must not grow a users table, a
> password column, or a signup form. A person has a Genmars account or they do
> not get in; if they do not have one they are sent to the portal to make one.
>
> That is what makes deactivating somebody mean something. `is_active` on one
> `User` row ends access to every company application at once. With a second
> account store, revoking access becomes a checklist — and a checklist is how a
> leaver keeps a login for eight months.

---

## What a sibling gets, and what it does not

It gets **one answer to one question**: who is the person holding this code.
Delivered once, in exchange for a code that dies on use, over a call it makes
from its own server with its own secret.

It does **not** get a token it can keep, a way to read anything from the portal,
a way to act as the person, or a way to ask about anybody else. There is no
refresh, no scope parameter, and no API behind this.

This is the same direction of trust the system registry already states: what
flows is inward. `SystemKey` lets a child report on itself; this lets a child
ask one question about a person standing in front of it. Neither reaches in.

### Deliberately not built

A sibling is told who somebody is **at the moment they sign in**, and nothing
afterwards. If that person is deactivated an hour later, the sibling's own
session keeps working until it expires.

Fixing that needs either the sibling re-asking on a timer or a revocation feed
it subscribes to. Neither is worth building before a second sibling exists —
so **keep sibling session lifetimes short**, and treat that as a condition of
being registered rather than something to discover afterwards.

---

## Registering an application

In the operations dashboard: **account menu → Engineering → Sibling sign-in**.
Founder only.

The application must already exist in the system registry — with an owner, a
stated criticality and a purpose. An application nobody owns and nobody
monitors should not be issuing our identities.

Four steps, in this order, and it is inert until all four are done:

1. **Register.** Issues a `client_id` (`gsso_…`). Public — it travels in a URL.
2. **Add the return address.** Live `https` only. See the rule below.
3. **Issue a secret** (`gsec_…`). Shown **once**; only an Argon2 hash is kept.
   Rotation has no overlap window — the old secret stops working immediately.
4. **Turn it on.** Refused while any of the above is missing, so "enabled" never
   means "enabled and still broken".

### The return address must be live https

Enforced by `validate_live_https_url` in `portal/models.py`, on the model and
at the API. Refused: plain `http`, `localhost`, `127.0.0.1`, any IP literal,
`.local` / `.test` / `.internal` / `.example` / `.invalid` / `.home` / `.lan`,
a bare hostname with no dot, a query string, a `#fragment`, and userinfo.

Why it is a hard rule rather than advice: a redirect address is where the portal
sends a person holding a one-time code that becomes their identity.
`http://localhost:3000/callback` registered on a production app is not a place —
it is "wherever the victim is". Plain `http` puts the code on the wire in clear.

To develop against this, register a real https host for a test application.

---

## The flow

```
person ──▶ business-os ──▶ app.genmars.co.ke/sign-on?client_id=…&redirect_uri=…&state=…
                                    │
                             signs in / already is, sees which app is asking,
                             presses Continue
                                    │
           business-os ◀── redirect_uri?code=…&state=…
                │
                └── SERVER ──▶ POST api.genmars.co.ke/api/auth/sign-on/token
                                    │
                             { account, organisations, issued_at }
```

### 1 — send the person to the portal

```
https://app.genmars.co.ke/sign-on
  ?client_id=gsso_xxxxxxxxxxxxxxxx
  &redirect_uri=https://business-os.genmars.co.ke/auth/callback
  &state=<your own random value>
```

`state` is **yours**. The portal echoes it back untouched, never parses it and
never decides anything from it. Generate it per attempt, keep it in the
visitor's session, and compare on return — that is your CSRF defence for this
flow and nothing on our side substitutes for it.

The portal shows the person which application is asking and where they are about
to be sent, and waits for a press. It never redirects automatically: without the
press, any page on the internet could link there and silently hand a signed-in
person's identity to an application they never agreed to join.

### 2 — the browser comes back

```
https://business-os.genmars.co.ke/auth/callback?code=gsc_…&state=…
```

Check the `state` first. Then take the code straight to step 3 — it lives
**90 seconds**.

### 3 — swap the code, from your server

```http
POST https://api.genmars.co.ke/api/auth/sign-on/token
Content-Type: application/json

{
  "client_id":     "gsso_…",
  "client_secret": "gsec_…",
  "code":          "gsc_…",
  "redirect_uri":  "https://business-os.genmars.co.ke/auth/callback"
}
```

`redirect_uri` must be the same one the code was issued for. Never make this
call from a browser — the secret belongs on your server only.

```json
{
  "account": {
    "id": 12,
    "email": "someone@genmars.co.ke",
    "full_name": "A Person",
    "is_staff": true,
    "staff_role": "delivery",
    "email_verified": true
  },
  "organisations": [{ "id": 3, "name": "Clips Serenity Spa" }],
  "issued_at": "2026-09-09T09:15:43.120416+00:00"
}
```

Start your own session from this. `staff_role` is `""` for a client account and
for a staff account whose role has not been decided — treat both as "no
authority".

### Failures

Every refusal from `/token` returns **400** with the same message:

```json
{ "detail": "That sign-in could not be completed. Start again." }
```

Wrong secret, unknown client, stale code, already-spent code, mismatched
redirect, deactivated account, application turned off — all identical on
purpose. A caller who learns that its secret was right but its code was stale
has learned which half to keep attacking. Retry the whole flow.

A code that has been **presented** is spent, even if the presentation was wrong.
Otherwise a code lifted from a log could be tried against every registered
address until one worked.

---

## Who may sign in

Three conditions. The first two are not configurable:

- an active account in the company accounts system
- a **verified** email address — an identity handed to another application must
  be one somebody proved they own
- the audience rule, which only decides whether *clients* also reach this app:
  - `staff` — Genmars staff only (default)
  - `any` — any verified account, staff or client

---

## Where the code lives

| Piece | File |
|---|---|
| Models, and the https rule | `backend/portal/models.py` — `SignOnApp`, `SignOnGrant`, `validate_live_https_url` |
| Issuing and redeeming | `backend/accounts/identity.py` — behind the identity boundary with every other auth operation |
| The three endpoints | `backend/accounts/views.py`, routed in `accounts/urls.py` |
| Founder-only configuration | `backend/operations/views.py` + `services.py` |
| Consent screen | `frontend/src/app/(auth)/sign-on/page.tsx` |
| Operations screen | `internals-tm/src/app/settings/engineering/page.tsx` |
| Tests | `backend/accounts/tests/test_sign_on.py`, `backend/operations/tests/test_sign_on_admin.py` |
