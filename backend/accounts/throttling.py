"""
Rate limits for the auth endpoints.

Two layers, and both are needed:

  identity.py  caps failures PER ACCOUNT — five wrong passwords locks that
               account for fifteen minutes.
  here         caps requests PER CLIENT — because the per-account lock does
               nothing against someone spraying one common password across a
               thousand different addresses. Each address sees one failure; the
               attacker sees a thousand attempts.

DRF's throttling is used rather than a third-party package: it is already in the
stack, it is backed by the cache we already configure, and Charter 03 §I is
explicit that a new dependency needs a reason.

The cache is Redis in production and locmem in tests. Note that locmem is
per-process, so throttle state is not shared across workers — for the volumes
this portal will see that is acceptable, and Redis is what actually runs.
"""

from __future__ import annotations

from rest_framework.throttling import SimpleRateThrottle


class ClientIPThrottle(SimpleRateThrottle):
    """
    Per-IP, scoped by endpoint.

    Behind Caddy every request arrives from 127.0.0.1, so the real client
    address comes from X-Forwarded-For. DRF reads that only when
    NUM_PROXIES is set — see settings. Getting this wrong would throttle the
    proxy rather than the caller, which means one attacker locks out everyone.
    """

    scope = "auth"

    def get_cache_key(self, request, view):
        ident = self.get_ident(request)
        scope = getattr(view, "throttle_scope", self.scope)
        return f"throttle:{scope}:{ident}"


class SignInThrottle(ClientIPThrottle):
    scope = "auth_sign_in"


class SignUpThrottle(ClientIPThrottle):
    scope = "auth_sign_up"


class CodeThrottle(ClientIPThrottle):
    """
    Covers requesting AND redeeming codes.

    Requesting is the one that costs us money and reputation — every request
    sends an email, so an unthrottled endpoint is a free way to use our domain
    to spam a stranger's inbox.
    """

    scope = "auth_code"


class EmailScopedThrottle(SimpleRateThrottle):
    """
    Per-EMAIL, regardless of source address.

    Stops a rotating-IP attacker from bypassing the per-IP limit on one target
    account, and stops anyone using the forgot-password form to repeatedly mail
    a person who did not ask for it.

    Keyed on the submitted address, so it applies whether or not that address
    has an account — the endpoint must behave identically either way.
    """

    scope = "auth_email"

    def get_cache_key(self, request, view):
        email = (request.data.get("email") or "").strip().lower()
        if not email:
            return None  # nothing to key on; the IP throttle still applies
        return f"throttle:email:{getattr(view, 'throttle_scope', self.scope)}:{email}"
