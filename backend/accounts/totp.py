"""
Time-based one-time passwords, RFC 6238, over the standard library.

═══════════════════════════════════════════════════════════════════════════════
WHAT THIS IS FOR, AND WHAT IT IS NOT FOR.

An admin account at Genmars reads every client's data. Until now the only thing
between a stolen password and all of it was the password — and `/admin/login/`
is reachable from anywhere on the internet, so the attacker does not need to be
near anybody.

A second factor fixes exactly one threat and it is the one that actually
happens: the password is known to somebody who should not know it. Phished,
reused on a site that was breached, typed into the wrong window, read over a
shoulder.

It does NOT protect a compromised database. The secret below is stored as
Django stores it, and an attacker holding the table holds the secrets as well
as the Argon2 hashes. That is not a gap being papered over — it is the normal
posture for TOTP everywhere, including django-otp, and it is honest about what
the control is for. A database compromise is already total; this is about
keeping a password from being enough.
═══════════════════════════════════════════════════════════════════════════════

── CHARTER 03 §I: NO DEPENDENCY, AND THIS TIME IT IS NOT CLOSE ────────────────

RFC 6238 is HMAC-SHA1 over a counter, truncated. `hmac`, `hashlib`, `struct`
and `base64` have all shipped with Python for twenty years, and the whole
algorithm is the twelve lines of `_code_at` below. django-otp would bring a
model layer, an admin integration and a migration history to do what this file
does in a page.

That reasoning does not generalise. `cryptography` was added to business-os for
AES-GCM precisely because the standard library has no symmetric encryption and
hand-rolling one is reckless. Reimplementing a KDF or a cipher is reckless;
reimplementing an HMAC truncation that the standard library already computes
for you is not.

── THE STEP COUNTER IS STORED, SO A CODE WORKS ONCE ───────────────────────────

Without that, a code is valid for its whole thirty-second window and anybody
who reads it over a shoulder — or phishes it along with the password — can use
it too, which removes most of the point. `StaffTotp.last_step` is the high
water mark and nothing at or below it is accepted again.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

# Thirty seconds is what every authenticator app assumes. It is not
# configurable because a server and a phone that disagree about it produce a
# code that is simply always wrong, with nothing on either side saying why.
STEP_SECONDS = 30
DIGITS = 6

# ── HOW MUCH CLOCK SKEW TO FORGIVE ──────────────────────────────────────────
#
# One step either side, so a phone up to thirty seconds out still works. Each
# extra step of tolerance multiplies the number of codes a guesser may hit, so
# this buys usability at a measurable cost: at ±1 there are three live codes
# out of a million, and the rate limit on the login form is what makes that
# number mean something.
SKEW_STEPS = 1

SECRET_BYTES = 20  # 160 bits, what RFC 4226 recommends and what apps expect.


def new_secret() -> str:
    """A fresh base32 secret, in the form an authenticator app expects."""
    return base64.b32encode(secrets.token_bytes(SECRET_BYTES)).decode().rstrip("=")


def _code_at(secret: str, step: int) -> str:
    """One code, for one time step. RFC 6238 §4, RFC 4226 §5.3."""
    # Apps show the secret without padding; b32decode insists on it.
    padded = secret.upper() + "=" * (-len(secret) % 8)
    key = base64.b32decode(padded, casefold=True)

    digest = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    truncated = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(truncated % (10**DIGITS)).zfill(DIGITS)


def current_step(at: float | None = None) -> int:
    return int((at if at is not None else time.time()) // STEP_SECONDS)


def verify(secret: str, code: str, *, after_step: int = 0,
           at: float | None = None) -> int | None:
    """
    Check a code. Returns the step it matched, or None.

    `after_step` is the last step this account has already spent; anything at
    or below it is refused however correct it looks, which is what stops a
    code being replayed inside its own window.

    ── CONSTANT TIME, AND NOT BECAUSE IT IS FASHIONABLE ────────────────────
    `compare_digest` rather than `==`. A six-digit code compared left to
    right leaks how many leading digits were right, and an attacker who can
    time the response can walk the code one digit at a time — a million
    guesses becomes sixty.
    """
    code = (code or "").strip().replace(" ", "")
    if not code.isdigit() or len(code) != DIGITS:
        return None

    now = current_step(at)
    for step in range(now - SKEW_STEPS, now + SKEW_STEPS + 1):
        if step <= after_step:
            continue
        if hmac.compare_digest(_code_at(secret, step), code):
            return step
    return None


def provisioning_uri(secret: str, *, account: str, issuer: str) -> str:
    """
    The otpauth:// URI an authenticator app reads from a QR code.

    ⚠ THIS STRING CONTAINS THE SECRET. It is printed once, by a management
    command, to a terminal the founder is sitting at. It must never be
    logged, emailed, or returned over HTTP — a secret that travels to a
    browser has been handed to anything that can read the page or the proxy
    in front of it.
    """
    label = quote(f"{issuer}:{account}", safe="")
    return (
        f"otpauth://totp/{label}"
        f"?secret={secret}&issuer={quote(issuer, safe='')}"
        f"&algorithm=SHA1&digits={DIGITS}&period={STEP_SECONDS}"
    )
