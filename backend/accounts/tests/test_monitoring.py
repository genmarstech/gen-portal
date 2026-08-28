"""
Tier 1: "error monitoring that reaches a human".

Configuration is not the same as delivery. These tests assert that an un-caught
exception in a view actually produces a message addressed to a person, and that
it does NOT carry client data with it. Both halves matter: an alert channel that
is silent is useless, and one that mails personal data to a mailbox is a leak
with a cron job.
"""

from __future__ import annotations

import pytest
from django.core import mail
from django.test import override_settings
from django.urls import path

from config import urls as root_urls


def _boom(request):
    raise RuntimeError("synthetic failure for the monitoring test")


urlpatterns = root_urls.urlpatterns + [path("api/boom", _boom, name="boom")]


@override_settings(
    ROOT_URLCONF=__name__,
    DEBUG=False,
    ALLOWED_HOSTS=["testserver"],
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
)
def test_an_unhandled_exception_emails_a_human(client, settings):
    """
    django.request logs at ERROR on a 500, and AdminEmailHandler turns that into
    mail. If this test goes quiet, nobody is being told when the portal breaks.
    """
    with pytest.raises(RuntimeError):
        client.get("/api/boom")

    assert len(mail.outbox) == 1, "a 500 produced no alert — nothing reaches a human"

    alert = mail.outbox[0]
    assert alert.to == [address for _, address in settings.ADMINS]
    assert "synthetic failure" in alert.body


@override_settings(
    ROOT_URLCONF=__name__,
    DEBUG=False,
    ALLOWED_HOSTS=["testserver"],
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
)
def test_the_alert_carries_no_html_traceback(client):
    """
    include_html=False is a privacy decision, not a formatting one.

    Django's HTML traceback embeds local variables from every frame. In this
    application that means session keys, email addresses and submitted form
    values — client personal data, mailed in plain text through a relay. The
    plain-text traceback locates the fault without shipping the contents.
    """
    # Explicit, so this cannot pass by reading an alert another test produced.
    mail.outbox.clear()

    with pytest.raises(RuntimeError):
        client.get("/api/boom")

    assert len(mail.outbox) == 1
    assert mail.outbox[0].alternatives == [], (
        "an HTML traceback would carry local variables"
    )


def test_debug_suppresses_alerts(client, settings):
    """
    RequireDebugFalse. Without it, a developer running locally mails a human
    every time they hit a bug they are in the middle of fixing.
    """
    settings.DEBUG = True
    mail.outbox.clear()

    with override_settings(
        ROOT_URLCONF=__name__,
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    ):
        with pytest.raises(RuntimeError):
            client.get("/api/boom")

    assert mail.outbox == []
