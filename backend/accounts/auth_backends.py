"""
The authentication backend the Django admin uses.

═══════════════════════════════════════════════════════════════════════════════
THE ADMIN LOGIN USED TO IGNORE EVERY PROTECTION THE PORTAL HAS.

`accounts/identity.py` enforces a five-attempt lockout with a fifteen-minute
expiry, and DRF throttles the portal's sign-in endpoint at ten attempts a
minute. Neither applied at `/admin/login/`.

Django's admin uses `AuthenticationForm`, which calls
`django.contrib.auth.authenticate`, which walks AUTHENTICATION_BACKENDS — and
that setting was unset, so it was the stock `ModelBackend`. `ModelBackend`
knows nothing about `User.locked_until`; it compares a hash and returns a user.
DRF's throttling is a DRF concern and an admin view is not a DRF view.

So the same accounts, with the same passwords, had one hardened door and one
open one — and the accounts reachable through the admin are staff and
superusers, which read across every organisation in the company. Argon2 made
each attempt expensive, which bounds the rate. It was the only thing in the way.

Finding 2 of docs/SECURITY-AUDIT-2026-09-09.md.
═══════════════════════════════════════════════════════════════════════════════

── WHY A BACKEND RATHER THAN A PATCHED FORM ────────────────────────────────────

A custom `AdminAuthenticationForm` would fix the admin and only the admin. A
backend fixes every caller of `django.contrib.auth.authenticate` there will
ever be — a management command, a future SSO shim, a third-party package that
authenticates its own way in. The rule becomes true where authentication
happens rather than true in the places somebody remembered.

── IT DELEGATES; IT DOES NOT REIMPLEMENT ───────────────────────────────────────

Every decision here is `identity.authenticate`'s. This class translates between
two conventions and nothing more: Django hands it `username` because that is
what `USERNAME_FIELD` is called in the abstract, and expects `None` for "no",
where identity raises. A second copy of the lockout rules living here would be
the exact sprawl the identity boundary exists to prevent — and the copy that
drifts is always the one nobody is looking at.

── FAILURES RETURN None, WHICH IS NOT A LOST MESSAGE ───────────────────────────

Django renders its own "Please enter the correct email and password" for a
`None`, identically for a wrong password, an unknown address, a locked account
and a deactivated one. That is the same uniformity `AuthError.safe_message`
gives the API: telling somebody at a login form that an account exists but is
locked confirms the address is real, which is the account-enumeration oracle in
a different costume.

The distinction is not lost, it is moved: `identity` records the failure and
advances the lockout counter before this returns.
"""

from __future__ import annotations

from django.contrib.auth.backends import ModelBackend

from . import identity

# ── THE PATH login() STAMPS INTO THE SESSION ────────────────────────────────
#
# `login(request, user, backend=...)` stores this string as
# `_auth_user_backend`, and every later request loads THAT class to resolve the
# session back to a user. A stale value does not raise: the session simply
# reads as signed out, which is how the whole suite failed the moment
# AUTHENTICATION_BACKENDS changed and five views were still naming
# ModelBackend by hand.
#
# So it is written once. `test_session_backend_path_matches_settings` asserts
# this equals the configured backend, because a typo here is a silent sign-out
# for everybody rather than an error anyone sees.
SESSION_BACKEND = "accounts.auth_backends.IdentityBackend"


class IdentityBackend(ModelBackend):
    """`ModelBackend`, but the password check goes through the boundary."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        # The admin form sends `username`, because that is what USERNAME_FIELD
        # is called in the abstract; `authenticate(email=...)` is what a reader
        # of this codebase would write. Accept both rather than making the
        # caller know which one this project chose.
        email = username or kwargs.get("email") or ""

        try:
            # Empty credentials go through rather than short-circuiting. It
            # looks wasteful and is not: identity burns a hash comparison when
            # no account matches, so a blank submission costs the same as a
            # real attempt on a real address. Returning early here would have
            # made "" measurably faster than a wrong password, which is a
            # timing oracle for nothing.
            #
            # ModelBackend does its own equalising with UserModel().set_password
            # — that call is inside the identity boundary, so this defers to
            # identity for it instead of keeping a second copy here. The
            # boundary check refused the first draft of this file for exactly
            # that, which is the check doing its job.
            return identity.authenticate(email, password or "")
        except identity.AuthError:
            # Includes AccountLocked, which subclasses it. The lockout has
            # already been recorded by identity; returning None is how a
            # backend says no.
            return None


# ═══════════════════════════════════════════════════════════════════════════
# THE OTHER HALF: A LIMIT ON THE DOOR, NOT ONLY ON THE ACCOUNT
# ═══════════════════════════════════════════════════════════════════════════
#
# The lockout above is per ACCOUNT, which is the right shape for somebody
# working through passwords against one address and the wrong shape for
# somebody working through addresses with one password. "Summer2026!" tried
# once each against forty staff addresses never trips a five-attempt counter,
# because no counter ever reaches two.
#
# So the address doing the trying is limited as well. Ten POSTs a minute: an
# order of magnitude above any honest use of a login form — nobody signs into
# the admin ten times a minute — and far below the rate that makes spraying
# worth doing.
#
# ── WHY django-ratelimit AND NOT DRF's THROTTLING ──────────────────────────
#
# DRF throttles are enforced by DRF's APIView, and the admin login is a plain
# Django view. django-ratelimit works on any view, has been a declared
# dependency in requirements.txt since before this file existed, and was
# imported precisely nowhere — a package carried for a year and never used
# (Charter 03 §I says a dependency earns its place; this is it earning it).
#
# ── IT COUNTS IN REDIS, WHICH MATTERS MORE THAN IT SOUNDS ──────────────────
#
# The rate lives in the default cache. In production that is Redis and is
# shared, so the limit holds across gunicorn workers and survives a redeploy.
# Fall back to the local-memory cache and each worker counts on its own,
# multiplying the real limit by the worker count. settings.CACHES is Redis
# whenever REDIS_URL is set; the deploy check asserts it, so this is not a
# silent degradation waiting to happen.

from django.contrib.auth.views import LoginView  # noqa: E402
from django_ratelimit.decorators import ratelimit  # noqa: E402

ADMIN_LOGIN_RATE = "10/m"


def rate_limited_admin_login(view):
    """
    Wrap the admin's login view in a per-IP rate limit.

    `block=True` returns 403 rather than letting the request through with a
    flag set. A login form is the one place where failing closed is
    uncontroversial: the cost of being wrong is a founder waiting sixty
    seconds, and the cost of the alternative is unbounded guessing.

    `method="POST"` so that merely LOADING the page is never limited — the
    admin redirects to it from every deep link, and a founder clicking a
    bookmarked URL while signed out must not spend the budget that is there to
    stop guessing.
    """
    return ratelimit(
        key="ip",
        rate=ADMIN_LOGIN_RATE,
        method="POST",
        block=True,
    )(view)
