"""
The Resend backend.

Every test here uses a fake transport. Nothing in this file may reach the
network: a suite that sends real email is a suite nobody can run offline, in
CI, or twice in a row — and the messages it sends carry live verification
codes to whatever address the fixture happened to name.

The rule these tests exist to defend is the one in mail_backends.py: a code is
a credential for fifteen minutes, and it must not end up in a log.
"""

from __future__ import annotations

import io
import json
import logging
import urllib.error

import pytest
from django.core.mail import EmailMessage
from django.test import override_settings

from accounts.mail_backends import ResendBackend

BACKEND = "accounts.mail_backends.ResendBackend"


class FakeResponse(io.BytesIO):
    """Enough of an HTTPResponse for a context manager and a read()."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def capture(monkeypatch):
    """Swap urlopen for a recorder. Returns the list of requests it saw."""
    seen = []

    def fake_urlopen(request, timeout=None):
        seen.append(
            {
                "url": request.full_url,
                "method": request.get_method(),
                "headers": {k.lower(): v for k, v in request.headers.items()},
                "body": json.loads(request.data.decode()),
                "timeout": timeout,
            }
        )
        return FakeResponse(b'{"id": "re_123"}')

    monkeypatch.setattr("accounts.mail_backends.urllib.request.urlopen", fake_urlopen)
    return seen


@override_settings(RESEND_API_KEY="re_test_key", DEFAULT_FROM_EMAIL="info@genmars.co.ke")
def test_sends_one_request_per_message(capture):
    backend = ResendBackend()
    sent = backend.send_messages(
        [
            EmailMessage("Your Genmars verification code", "Your code is 123456", to=["a@b.co.ke"]),
            EmailMessage("Reset your Genmars password", "Your reset code is 654321", to=["c@d.co.ke"]),
        ]
    )

    assert sent == 2
    assert len(capture) == 2
    assert capture[0]["url"] == "https://api.resend.com/emails"
    assert capture[0]["method"] == "POST"


@override_settings(RESEND_API_KEY="re_test_key", DEFAULT_FROM_EMAIL="info@genmars.co.ke")
def test_builds_the_payload_resend_expects(capture):
    ResendBackend().send_messages(
        [EmailMessage("Subject", "Body", "info@genmars.co.ke", ["client@example.co.ke"])]
    )

    body = capture[0]["body"]
    assert body == {
        "from": "info@genmars.co.ke",
        "to": ["client@example.co.ke"],
        "subject": "Subject",
        "text": "Body",
    }
    assert capture[0]["headers"]["authorization"] == "Bearer re_test_key"
    assert capture[0]["headers"]["content-type"] == "application/json"


@override_settings(RESEND_API_KEY="re_test_key")
def test_every_recipient_is_addressed(capture):
    """cc and bcc are recipients too. Dropping them loses mail silently."""
    ResendBackend().send_messages(
        [
            EmailMessage(
                "S", "B", "info@genmars.co.ke",
                to=["one@x.co.ke"], cc=["two@x.co.ke"], bcc=["three@x.co.ke"],
            )
        ]
    )
    assert set(capture[0]["body"]["to"]) == {"one@x.co.ke", "two@x.co.ke", "three@x.co.ke"}


@override_settings(RESEND_API_KEY="re_test_key", EMAIL_TIMEOUT=7)
def test_the_timeout_is_applied(capture):
    """
    An unbounded request holds a gunicorn worker until the OS gives up. Enough
    of those at once and the portal stops answering for reasons that have
    nothing to do with the portal.
    """
    ResendBackend().send_messages([EmailMessage("S", "B", "info@genmars.co.ke", ["a@b.co.ke"])])
    assert capture[0]["timeout"] == 7


@override_settings(RESEND_API_KEY="")
def test_a_missing_key_is_loud(capture):
    """
    The whole point of the boot guard, restated at the last possible moment.
    Returning 0 quietly here would mean sign-up succeeds and no code is sent.
    """
    with pytest.raises(ValueError, match="RESEND_API_KEY"):
        ResendBackend().send_messages([EmailMessage("S", "B", "info@genmars.co.ke", ["a@b.co.ke"])])
    assert capture == []


@override_settings(RESEND_API_KEY="")
def test_a_missing_key_is_silent_only_when_asked(capture):
    assert ResendBackend(fail_silently=True).send_messages(
        [EmailMessage("S", "B", "info@genmars.co.ke", ["a@b.co.ke"])]
    ) == 0


@override_settings(RESEND_API_KEY="re_test_key")
def test_no_messages_makes_no_requests(capture):
    assert ResendBackend().send_messages([]) == 0
    assert capture == []


@override_settings(RESEND_API_KEY="re_test_key")
def test_an_http_error_raises_and_does_not_pretend(monkeypatch):
    def boom(request, timeout=None):
        raise urllib.error.HTTPError(
            "https://api.resend.com/emails", 403, "Forbidden", {},
            io.BytesIO(b'{"message": "The genmars.co.ke domain is not verified."}'),
        )

    monkeypatch.setattr("accounts.mail_backends.urllib.request.urlopen", boom)
    with pytest.raises(urllib.error.HTTPError):
        ResendBackend().send_messages([EmailMessage("S", "B", "info@genmars.co.ke", ["a@b.co.ke"])])


@override_settings(RESEND_API_KEY="re_test_key")
def test_a_network_error_raises(monkeypatch):
    def boom(request, timeout=None):
        raise urllib.error.URLError("name resolution failed")

    monkeypatch.setattr("accounts.mail_backends.urllib.request.urlopen", boom)
    with pytest.raises(urllib.error.URLError):
        ResendBackend().send_messages([EmailMessage("S", "B", "info@genmars.co.ke", ["a@b.co.ke"])])


@override_settings(RESEND_API_KEY="re_test_key")
def test_fail_silently_swallows_a_transport_error(monkeypatch):
    def boom(request, timeout=None):
        raise urllib.error.URLError("unreachable")

    monkeypatch.setattr("accounts.mail_backends.urllib.request.urlopen", boom)
    assert ResendBackend(fail_silently=True).send_messages(
        [EmailMessage("S", "B", "info@genmars.co.ke", ["a@b.co.ke"])]
    ) == 0


@override_settings(RESEND_API_KEY="re_secret_key_value")
def test_nothing_secret_reaches_the_logs(monkeypatch, caplog):
    """
    THE test in this file.

    A verification code is a credential. So is the API key. Neither may appear
    in a log line — not on success, and not on the error path, which is exactly
    where someone reaches for "just log the payload" while debugging.
    """
    def boom(request, timeout=None):
        raise urllib.error.HTTPError(
            "https://api.resend.com/emails", 422, "Unprocessable", {},
            io.BytesIO(b'{"message": "Invalid to field."}'),
        )

    monkeypatch.setattr("accounts.mail_backends.urllib.request.urlopen", boom)

    with caplog.at_level(logging.DEBUG):
        ResendBackend(fail_silently=True).send_messages(
            [EmailMessage("Your Genmars verification code", "Your code is 481625",
                          "info@genmars.co.ke", ["a@b.co.ke"])]
        )

    logged = caplog.text
    assert "481625" not in logged
    assert "re_secret_key_value" not in logged
    assert "Your code is" not in logged
    # It must still say something useful, or the guarantee costs us debugging.
    assert "422" in logged


@override_settings(RESEND_API_KEY="re_test_key")
def test_success_logs_the_id_and_nothing_else(capture, caplog):
    with caplog.at_level(logging.DEBUG):
        ResendBackend().send_messages(
            [EmailMessage("Your Genmars verification code", "Your code is 999111",
                          "info@genmars.co.ke", ["a@b.co.ke"])]
        )
    assert "re_123" in caplog.text
    assert "999111" not in caplog.text


@override_settings(EMAIL_BACKEND=BACKEND, RESEND_API_KEY="re_test_key")
def test_django_send_mail_routes_through_it(capture):
    """
    The integration that matters: accounts/emails.py calls django.core.mail,
    not this class. If the backend is not wired in correctly, every test above
    passes and no client ever gets a code.
    """
    from accounts import emails

    emails.send_verification_code("client@example.co.ke", "246810")

    assert len(capture) == 1
    assert capture[0]["body"]["to"] == ["client@example.co.ke"]
    assert capture[0]["body"]["subject"] == "Your Genmars verification code"
    assert "246810" in capture[0]["body"]["text"]
