"""
Is our outbound mail actually being delivered?

═══════════════════════════════════════════════════════════════════════════════
WHY THIS FILE EXISTS, IN ONE STORY.

On 2026-09-01 a Django error alert went to info@genmars.co.ke. Zoho answered
`550 5.1.1 User does not exist` — the mailbox had not been created yet. Resend
recorded the hard bounce and added the address to its SUPPRESSION LIST, which is
what a sending provider does to protect its reputation.

From that moment every email to that address was dropped inside Resend. The API
still answered 200. Our backend still logged "resend accepted message id=...".
Nothing errored anywhere. Error alerts, backup-failure alerts and uptime alerts
all went nowhere for a day and a half, and the only visible symptom was an inbox
that stayed empty — which reads as "nothing has gone wrong".

A suppressed address is silent by design. This makes it loud.
═══════════════════════════════════════════════════════════════════════════════

── IT MUST NOT REPORT BY EMAIL ─────────────────────────────────────────────────

The obvious place to put this check is the alert mail. That cannot work: the
failure being detected is that alert mail does not arrive. So the result is
surfaced in the operations app, where a human signs in and looks — the one
channel that does not depend on the channel that is broken.

── CLIENT ADDRESSES MATTER AS MUCH AS OURS ─────────────────────────────────────

A client whose address bounced once is suppressed too, and would then never
receive a verification code again — every attempt silently dropped, while they
tell us the code "never arrives". So the whole list is reported, with our own
addresses called out rather than filtered to.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

from django.conf import settings
from django.core.cache import cache

log = logging.getLogger(__name__)

ENDPOINT = "https://api.resend.com/suppressions"
CACHE_KEY = "mail-health:suppressions"
# Long enough that the operations dashboard does not make an outbound HTTPS
# call on every page load, short enough that clearing a suppression shows up
# while the person who cleared it is still looking at the screen.
CACHE_SECONDS = 300
USER_AGENT = "gen-portal (+https://genmars.co.ke)"


def our_addresses() -> set[str]:
    """The addresses Genmars sends alerts to. Any of these being blocked is ours."""
    return {
        address.lower()
        for address in (
            getattr(settings, "DEFAULT_FROM_EMAIL", ""),
            getattr(settings, "SERVER_EMAIL", ""),
            getattr(settings, "ERROR_EMAIL", ""),
        )
        if address
    }


def mail_health(*, force: bool = False) -> dict:
    """
    What Resend is refusing to deliver to.

    Never raises. A dashboard that 500s because a third party is slow is worse
    than one that says it could not check — so an unreachable Resend reports
    `checked: False` and the caller renders that honestly rather than showing a
    reassuring green.
    """
    if not force:
        cached = cache.get(CACHE_KEY)
        if cached is not None:
            return cached

    result = _fetch()
    cache.set(CACHE_KEY, result, CACHE_SECONDS)
    return result


def _fetch() -> dict:
    key = getattr(settings, "RESEND_API_KEY", "")
    if not key:
        # Local development and CI. Not a fault, and not something to alarm
        # anybody about — say plainly that there was nothing to check.
        return {"checked": False, "reason": "No Resend key configured.", "blocked": []}

    request = urllib.request.Request(
        ENDPOINT,
        headers={"Authorization": f"Bearer {key}", "User-Agent": USER_AGENT},
    )

    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            payload = json.loads(response.read().decode("utf-8") or "{}")
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        # `exc` describes the transport. The key is never interpolated here.
        log.warning("could not read the suppression list: %s", exc)
        return {"checked": False, "reason": "Could not reach Resend.", "blocked": []}

    ours = our_addresses()
    blocked = []
    for entry in payload.get("data", []):
        address = (entry.get("email") or "").lower()
        blocked.append(
            {
                "email": address,
                "origin": entry.get("origin", ""),
                "since": entry.get("created_at", ""),
                # A blocked Genmars address means our own alerting is dead. A
                # blocked client address means one client silently never gets
                # a code. Different problems, both worth showing.
                "is_ours": address in ours,
            }
        )

    blocked.sort(key=lambda row: (not row["is_ours"], row["email"]))
    return {"checked": True, "reason": "", "blocked": blocked}
