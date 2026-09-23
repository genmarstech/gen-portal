"""
Talking to Google. Protocol only — this module never touches a User.

═══════════════════════════════════════════════════════════════════════════════
WHY THIS IS NOT IN identity.py

The identity boundary is about who somebody is and what may create or verify
that. This file is an HTTP client for somebody else's API: it builds a URL,
posts a form, and reads a JSON body back. It imports no model and calls nothing
in `identity`.

The split is what keeps `authenticate_google` in identity.py three lines of
rules instead of three lines of rules buried in fifty lines of urllib. When
AuthGate lands, the rules move and this file either moves with them or is
deleted — and either way nothing has to be untangled first.
═══════════════════════════════════════════════════════════════════════════════

── NO DEPENDENCY WAS ADDED FOR THIS. Charter 03 §I. ─────────────────────────

`urllib.request` posts a form and reads JSON, which is the whole of the token
exchange. The usual reason to reach for a library here is verifying the ID
token's RSA signature, and the section on that below explains why this flow
does not have to.
"""

from __future__ import annotations

import base64
import json
import secrets
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.conf import settings

AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"

# The two Google spells `iss` as, both legitimate, both seen in the wild.
ISSUERS = frozenset({"https://accounts.google.com", "accounts.google.com"})

# Identity only. No Drive, no Gmail, no Calendar. A scope is a thing somebody
# has to consent to and a thing a leaked token can do, and this flow needs
# neither more than an address and a name.
SCOPES = "openid email profile"

# Google is answering a server we control, over TLS, in a request we initiated.
# If it is slow, the person is staring at a spinner mid-sign-in.
TIMEOUT_SECONDS = 10


class GoogleError(Exception):
    """
    Google could not be asked, or did not answer usefully.

    Deliberately one exception with no subclasses. The caller's only sensible
    response to every variety is the same — send the person back to sign in —
    and the detail belongs in our log, not in a branch.
    """


@dataclass(frozen=True)
class GoogleIdentity:
    """What Google asserts about the person who just signed in."""

    email: str
    email_verified: bool
    full_name: str


def is_configured() -> bool:
    """
    Whether Google sign-in can work at all.

    The views 404 when this is false rather than erroring. An endpoint that
    exists and is broken invites somebody to debug it; one that is not there
    says the feature is off, which is the truth.
    """
    return bool(
        settings.GOOGLE_OAUTH_CLIENT_ID
        and settings.GOOGLE_OAUTH_CLIENT_SECRET
        and settings.GOOGLE_OAUTH_REDIRECT_URI
    )


def new_state() -> str:
    """
    The value that ties a callback to the browser that started it.

    Without it, anybody can send a victim a crafted callback URL and log them
    into the ATTACKER's account — login CSRF. The victim then works in what
    they believe is their own account. It is the quiet one of the OAuth
    failures, because nothing looks broken.
    """
    return secrets.token_urlsafe(32)


def authorization_url(*, state: str) -> str:
    return AUTH_ENDPOINT + "?" + urlencode(
        {
            "client_id": settings.GOOGLE_OAUTH_CLIENT_ID,
            "redirect_uri": settings.GOOGLE_OAUTH_REDIRECT_URI,
            "response_type": "code",
            "scope": SCOPES,
            "state": state,
            # We want an address, not standing access. No refresh token is
            # requested and none is stored: there is nothing here to leak
            # later, and nothing to remember to revoke.
            "access_type": "online",
            # Ask Google to pre-fill nothing. Left off, a shared browser
            # silently reuses whoever signed in last.
            "prompt": "select_account",
        }
    )


def exchange_code(code: str) -> GoogleIdentity:
    """
    Swap the one-time code for Google's claims about the person.

    Raises GoogleError for every failure, including a malformed answer.
    """
    body = urlencode(
        {
            "code": code,
            "client_id": settings.GOOGLE_OAUTH_CLIENT_ID,
            "client_secret": settings.GOOGLE_OAUTH_CLIENT_SECRET,
            "redirect_uri": settings.GOOGLE_OAUTH_REDIRECT_URI,
            "grant_type": "authorization_code",
        }
    ).encode()

    request = Request(
        TOKEN_ENDPOINT,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )

    try:
        with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read().decode())
    except HTTPError as e:
        # Google puts the reason in the body, and it is worth having in the
        # log — "redirect_uri_mismatch" is otherwise hours of guessing.
        detail = ""
        try:
            detail = e.read().decode()[:300]
        except Exception:
            pass
        raise GoogleError(f"token endpoint {e.code}: {detail}") from e
    except (URLError, TimeoutError, json.JSONDecodeError) as e:
        raise GoogleError(f"token endpoint unreachable: {e}") from e

    id_token = payload.get("id_token")
    if not id_token:
        raise GoogleError("no id_token in the token response")

    return _claims(id_token)


def _claims(id_token: str) -> GoogleIdentity:
    """
    Read the ID token.

    ═══════════════════════════════════════════════════════════════════════════
    THE SIGNATURE IS NOT CHECKED, AND THAT IS ONLY SAFE BECAUSE OF WHERE THIS
    TOKEN CAME FROM.

    This token was not handed to us by a browser. `exchange_code` fetched it
    from Google's own token endpoint, over TLS, in a request we made, using a
    client secret only we hold. The transport is what authenticates it — the
    signature would be re-proving what the TLS channel already proved. Google's
    own documentation says so for exactly this flow.

    ⚠ THAT ARGUMENT DIES THE MOMENT AN ID TOKEN ARRIVES FROM ANYWHERE ELSE.
      If a future change accepts an id_token posted by a browser — the Google
      Identity Services button does this — the signature becomes the ONLY thing
      standing between a forged token and a signed-in session, and this
      function is then a straightforward account-takeover bug. Verifying it
      needs a JWKS fetch and RSA verification, which needs a dependency, which
      needs Charter 03 §I answered properly. Do not paper over it here.

    The `aud` and `iss` checks below are defence in depth, not the main
    argument. They cost nothing and they catch a token that is genuinely
    Google's but issued for a different application.
    ═══════════════════════════════════════════════════════════════════════════
    """
    parts = id_token.split(".")
    if len(parts) != 3:
        raise GoogleError("id_token is not a JWT")

    try:
        # Base64url without padding. The `=` * 4 is over-padding, which the
        # decoder ignores; under-padding is what raises.
        raw = base64.urlsafe_b64decode(parts[1] + "=" * 4)
        claims = json.loads(raw.decode())
    except Exception as e:
        raise GoogleError(f"id_token payload unreadable: {e}") from e

    if claims.get("aud") != settings.GOOGLE_OAUTH_CLIENT_ID:
        raise GoogleError("id_token was issued for another application")

    if claims.get("iss") not in ISSUERS:
        raise GoogleError(f"unexpected issuer: {claims.get('iss')!r}")

    email = (claims.get("email") or "").strip().lower()
    if not email:
        raise GoogleError("id_token carries no email")

    # Google sends this as a real bool on the ID token, but has historically
    # sent the string "true" on other surfaces. Accept both rather than treat
    # a verified address as unverified.
    verified = claims.get("email_verified")
    verified = verified is True or verified == "true"

    return GoogleIdentity(
        email=email,
        email_verified=verified,
        full_name=(claims.get("name") or "").strip(),
    )
