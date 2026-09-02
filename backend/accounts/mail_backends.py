"""
Resend email backend.

Django's SMTP backend would also reach Resend — they publish an SMTP endpoint —
so this file has to justify itself. Two reasons, and the first is decisive:

  1. **Outbound SMTP is not reliably available.** Hetzner blocks outbound 25 on
     every account and 465/587 on new ones until support unblocks them. That
     failure is slow (a ten-second connect timeout per message) and looks
     nothing like "your mail provider is unreachable" in a log. HTTPS on 443
     works from any container on any host, on the first deploy, with no ticket.

  2. **A failure says what failed.** Resend answers with a status code and a
     message, and a success carries an id that can be pasted into their
     dashboard to see what happened to that exact email. SMTP gives a socket
     error and a 250, and a 250 only means the relay accepted it.

── NO NEW DEPENDENCY ────────────────────────────────────────────────────────
Charter 03 §I: something enters the stack only when what is already in it
genuinely cannot do the job. Resend publish a `resend` package; this is one
JSON POST with a bearer token, which urllib has done since Python 2. So
requirements.txt is unchanged, and there is no third-party code in the path
that verification codes travel down.

── WHAT THIS MUST NEVER DO ──────────────────────────────────────────────────
**Never log the message body.** It carries a six-digit code that is a live
credential for fifteen minutes. Errors here log the status, Resend's own error
text, and the recipient — never the subject line's contents and never the body.
That is why the except clauses below are narrow and re-raise rather than
dumping the message for debugging.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

from django.conf import settings
from django.core.mail.backends.base import BaseEmailBackend

log = logging.getLogger(__name__)

ENDPOINT = "https://api.resend.com/emails"

# ── THIS HEADER IS LOAD-BEARING. DO NOT REMOVE IT. ──────────────────────────
# Resend sits behind Cloudflare, and Cloudflare blocks urllib's default
# User-Agent ("Python-urllib/3.x") outright: every request comes back
# 403 "error code: 1010" — a Cloudflare page, not a Resend error, so the body
# says nothing about mail and there is no entry in the Resend dashboard to find.
# Verified from the production container: with no User-Agent, 403; with any
# ordinary one, the request reaches Resend and is answered normally.
#
# Identifying rather than impersonating a browser: if this ever gets blocked
# again, we want them able to see who it is.
USER_AGENT = "gen-portal (+https://genmars.co.ke)"


class ResendBackend(BaseEmailBackend):
    """
    Send through Resend's HTTP API.

    Stateless: there is no connection to open or close, so open() and close()
    are inherited as no-ops. Each message is one request, which is also what
    makes a partial failure sane — four codes go out, one fails, and the three
    recipients who got theirs are not rolled back.
    """

    def __init__(self, fail_silently: bool = False, **kwargs) -> None:
        super().__init__(fail_silently=fail_silently)
        # Read from settings, not the environment, so tests and management
        # commands can override it the ordinary Django way.
        self.api_key: str = getattr(settings, "RESEND_API_KEY", "")
        self.timeout: int = getattr(settings, "EMAIL_TIMEOUT", 10)

    def send_messages(self, email_messages) -> int:
        if not email_messages:
            return 0

        if not self.api_key:
            # Not silently. A missing key here means every verification code
            # this process ever sends is dropped, and the client waits forever
            # for a message nobody sent — the exact failure the boot guard in
            # settings.py exists to make impossible.
            if self.fail_silently:
                return 0
            raise ValueError(
                "RESEND_API_KEY is empty. The Resend backend cannot send mail "
                "without it."
            )

        sent = 0
        for message in email_messages:
            if self._send(message):
                sent += 1
        return sent

    @staticmethod
    def _html_alternative(message) -> str | None:
        """
        The text/html part, if the caller attached one.

        Reads `alternatives` defensively: a plain EmailMessage has no such
        attribute, and the entries are (content, mimetype) tuples on Django 5.1
        but a dataclass on newer versions. Getting this wrong drops the HTML
        rather than raising, which is exactly the failure this method exists to
        end, so both shapes are handled.
        """
        for alternative in getattr(message, "alternatives", None) or []:
            content = getattr(alternative, "content", None)
            mimetype = getattr(alternative, "mimetype", None)
            if content is None:
                try:
                    content, mimetype = alternative
                except (TypeError, ValueError):  # pragma: no cover - defensive
                    continue
            if mimetype == "text/html":
                return content
        return None

    def _send(self, message) -> bool:
        recipients = message.recipients()
        if not recipients:
            return False

        payload = {
            "from": message.from_email or settings.DEFAULT_FROM_EMAIL,
            "to": recipients,
            "subject": message.subject,
            "text": message.body,
        }

        # ── THE HTML PART ───────────────────────────────────────────────────
        # Django carries alternatives on the message; this backend used to send
        # only `text`, so an HTML part attached by the caller was accepted
        # without complaint and silently thrown away. Anything built on
        # EmailMultiAlternatives would have looked like it worked.
        #
        # `text` is still always sent and is still written to be read on its
        # own: it is what a plain-text client, a screen reader and a spam
        # filter see, and a verification code that only exists inside a <div>
        # is a code some recipients cannot use.
        html = self._html_alternative(message)
        if html is not None:
            payload["html"] = html

        if message.reply_to:
            payload["reply_to"] = list(message.reply_to)

        request = urllib.request.Request(
            ENDPOINT,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "User-Agent": USER_AGENT,
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = json.loads(response.read().decode("utf-8") or "{}")
            # Logged deliberately, and it is safe: an id is an opaque handle,
            # not content. It is the only way to trace one client's missing code
            # in the Resend dashboard without asking them to read it out.
            log.info("resend accepted message id=%s", body.get("id"))
            return True

        except urllib.error.HTTPError as exc:
            # Resend puts a usable reason in the body — an unverified sending
            # domain, an invalid key, a rate limit. Read it; it is the whole
            # value of using the API over SMTP. It contains our error, not our
            # message, so it is safe to log.
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            log.error(
                "resend rejected message: HTTP %s %s", exc.code, detail
            )
            if not self.fail_silently:
                raise
            return False

        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            # Network unreachable, DNS failure, TLS problem, or a response that
            # was not JSON. `exc` here describes the transport, never the
            # message — no body is interpolated into this line.
            log.error("resend unreachable: %s", exc)
            if not self.fail_silently:
                raise
            return False
