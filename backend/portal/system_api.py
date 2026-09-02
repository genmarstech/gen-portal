"""
The door registered systems report in through.

═══════════════════════════════════════════════════════════════════════════════
THIS IS THE ONLY ENDPOINT IN THE PROJECT AUTHENTICATED BY A BEARER TOKEN.

Everything else here is a session cookie belonging to a person. These two views
are different: they are called by MACHINES, from other applications, possibly
on other hosts, with no human present. That makes them the widest surface the
portal exposes, and they are written to be narrow in exchange:

  · A key identifies ONE system and can only speak about that system. There is
    no key that can report on behalf of another, and no admin key.
  · The only things a key can do are report health and post an event. It cannot
    read anything — not its own system, not another, and certainly not client
    data. A leaked key leaks nothing; it lets somebody write noise into one
    system's event feed, which a human then reads and disbelieves.
  · Nothing posted here is ever executed, interpolated into a query, or used to
    build a path. See SystemEvent's docstring.

── WHY NOT A USER ACCOUNT PER SYSTEM ───────────────────────────────────────────

Because a user account carries permissions that grow. The day somebody adds a
capability to "all staff" or "all authenticated users", every service account
silently gains it. A key that is not a user cannot be swept up in that.
═══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import logging
import secrets

from django.contrib.auth.hashers import check_password, make_password
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from .models import System, SystemEvent, SystemKey

log = logging.getLogger(__name__)

# Long enough that guessing is hopeless, and prefixed so a leaked one is
# recognisable in a log or a paste — "gms_" says what it is and where to revoke
# it, which shortens the time between a leak and its revocation.
TOKEN_BYTES = 32
TOKEN_PREFIX = "gms_"
PREFIX_LENGTH = 12


def issue_key(*, system: System, label: str, actor=None) -> tuple[SystemKey, str]:
    """
    Mint a key. Returns the row and the token, which is never recoverable again.

    The caller MUST show the token to the person once and must not store it,
    log it, or put it in a response that gets cached.
    """
    token = TOKEN_PREFIX + secrets.token_urlsafe(TOKEN_BYTES)
    key = SystemKey.objects.create(
        system=system,
        label=label.strip() or "unlabelled",
        prefix=token[:PREFIX_LENGTH],
        hashed=make_password(token),
        created_by=actor,
    )
    # The label and the system, never the token.
    log.info("system key issued for %s (%s)", system.slug, key.label)
    return key, token


def authenticate(token: str) -> SystemKey | None:
    """
    Resolve a presented token to an active key, or None.

    ── WHY THE PREFIX LOOKUP IS SAFE ───────────────────────────────────────────

    The prefix narrows the candidates; the hash comparison decides. Knowing a
    prefix proves nothing — it is 12 characters of a token with 32 bytes of
    entropy behind it, and every candidate it selects is still checked with a
    constant-time password verifier.

    Returns None for every failure — unknown prefix, wrong token, revoked key —
    and never says which. A caller learning "that prefix exists" would be
    learning something.
    """
    if not token or not token.startswith(TOKEN_PREFIX):
        return None

    candidates = SystemKey.objects.select_related("system").filter(
        prefix=token[:PREFIX_LENGTH], revoked_at__isnull=True
    )
    for key in candidates:
        if check_password(token, key.hashed):
            return key
    return None


class SystemKeyThrottle(ScopedRateThrottle):
    """
    Per-key, not per-IP.

    A system reporting from a container has whatever IP its host has, often
    shared with every other system we run. Throttling on that would let one
    noisy child silence the rest.
    """

    scope = "system"

    def get_cache_key(self, request, view):
        key = getattr(request, "system_key", None)
        if key is None:
            return None
        return f"throttle_system_{key.pk}"


class SystemView(APIView):
    """
    Base for the machine-facing endpoints.

    AllowAny at the DRF layer because the credential is not a session; the real
    check is `resolve` below, and every subclass calls it first.
    """

    permission_classes = [AllowAny]
    throttle_scope = "system"
    throttle_classes = [SystemKeyThrottle]

    def resolve(self, request) -> SystemKey | None:
        header = request.headers.get("Authorization", "")
        token = header[7:].strip() if header.lower().startswith("bearer ") else ""

        key = authenticate(token)
        if key is None:
            return None

        request.system_key = key
        return key

    @staticmethod
    def refused() -> Response:
        # One message for every failure mode. Distinguishing "no such key" from
        # "revoked" would tell an attacker which half of the problem to solve.
        return Response(
            {"detail": "Not a valid system key."},
            status=status.HTTP_401_UNAUTHORIZED,
        )


class HeartbeatView(SystemView):
    """
    "I am running." Called on a timer by the system itself.

    Complements the health poll rather than replacing it. A poll proves WE can
    reach it from here; a heartbeat proves it is running even when we cannot —
    behind a firewall, on a laptop, inside somebody else's network. Both being
    present is how a network problem is told apart from an outage.
    """

    def post(self, request):
        key = self.resolve(request)
        if key is None:
            return self.refused()

        system = key.system
        now = timezone.now()

        system.heartbeat_at = now
        # Whatever it says of itself. Bounded and stored as text; never parsed,
        # compared for ordering, or used to decide anything.
        version = str(request.data.get("version", ""))[:60]
        if version:
            system.version = version

        # A child may report itself unwell. Accepted as a claim about itself,
        # which is the only thing it is authoritative about.
        reported = str(request.data.get("health", "")).lower()
        if reported in {System.Health.UP, System.Health.DEGRADED, System.Health.DOWN}:
            system.health = reported
            system.health_detail = str(request.data.get("detail", ""))[:300]
            system.checked_at = now

        system.save(
            update_fields=[
                "heartbeat_at", "version", "health", "health_detail", "checked_at"
            ]
        )

        SystemKey.objects.filter(pk=key.pk).update(last_used_at=now)

        return Response({"ok": True, "system": system.slug})


class SystemEventView(SystemView):
    """
    "Something happened here." Recorded, shown to a person, and acted on by
    nobody automatically — see SystemEvent's docstring.
    """

    def post(self, request):
        key = self.resolve(request)
        if key is None:
            return self.refused()

        message = str(request.data.get("message", "")).strip()[:300]
        if not message:
            return Response(
                {"detail": "An event needs a message."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        level = str(request.data.get("level", "")).lower()
        if level not in SystemEvent.Level.values:
            level = SystemEvent.Level.INFO

        # Only a JSON object, and only one level deep in terms of what we
        # promise to render. Anything else is dropped rather than stored, so a
        # child cannot fill the column with a megabyte of nesting.
        detail = request.data.get("detail")
        if not isinstance(detail, dict) or len(str(detail)) > 4000:
            detail = {}

        event = SystemEvent.objects.create(
            system=key.system,
            level=level,
            message=message,
            detail=detail,
            occurred_at=None,
        )

        SystemKey.objects.filter(pk=key.pk).update(last_used_at=timezone.now())

        return Response(
            {"ok": True, "id": event.pk}, status=status.HTTP_201_CREATED
        )
