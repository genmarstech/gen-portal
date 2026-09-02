"""
The suppression detector.

Written because the bug it detects was silent for a day and a half: Resend
answered 200, the backend logged "accepted", and every alert went nowhere.
The tests below pin the two properties that make it useful — it notices, and it
never takes the dashboard down with it.
"""

from __future__ import annotations

import json
import urllib.error
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone

from accounts import mail_health as module
from accounts.models import User

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def staff() -> User:
    return User.objects.create_user(
        email="ops@genmars.co.ke", password="correct-horse-battery",
        full_name="Ops", is_staff=True, staff_role=User.StaffRole.FOUNDER,
        email_verified_at=timezone.now(),
    )


class _Response:
    def __init__(self, payload):
        self._payload = json.dumps(payload).encode()

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _resend(payload):
    return patch.object(module.urllib.request, "urlopen", lambda *a, **k: _Response(payload))


def test_a_blocked_company_address_is_reported_and_marked_as_ours(settings):
    settings.RESEND_API_KEY = "test-key"
    settings.DEFAULT_FROM_EMAIL = "info@genmars.co.ke"

    payload = {"data": [{
        "email": "info@genmars.co.ke", "origin": "bounce",
        "created_at": "2026-09-01 12:43:01+00",
    }]}

    with _resend(payload):
        health = module.mail_health(force=True)

    assert health["checked"] is True
    assert len(health["blocked"]) == 1
    assert health["blocked"][0]["is_ours"] is True
    assert health["blocked"][0]["origin"] == "bounce"


def test_a_blocked_client_address_is_reported_too(settings):
    """
    Not just ours. A client suppressed after one bounce silently never receives
    a verification code again, while telling us it "never arrives".
    """
    settings.RESEND_API_KEY = "test-key"
    settings.DEFAULT_FROM_EMAIL = "info@genmars.co.ke"

    payload = {"data": [{
        "email": "mercy@kilimanidental.co.ke", "origin": "bounce",
        "created_at": "2026-09-01 12:43:01+00",
    }]}

    with _resend(payload):
        health = module.mail_health(force=True)

    assert health["blocked"][0]["is_ours"] is False


def test_our_own_addresses_sort_first(settings):
    settings.RESEND_API_KEY = "test-key"
    settings.DEFAULT_FROM_EMAIL = "info@genmars.co.ke"

    payload = {"data": [
        {"email": "aaa@client.example", "origin": "bounce", "created_at": "x"},
        {"email": "info@genmars.co.ke", "origin": "bounce", "created_at": "x"},
    ]}

    with _resend(payload):
        health = module.mail_health(force=True)

    assert health["blocked"][0]["email"] == "info@genmars.co.ke"


def test_an_unreachable_resend_never_takes_the_dashboard_down(settings):
    """
    A dashboard that 500s because a third party is slow is worse than one that
    says it could not check. It must not claim everything is fine either.
    """
    settings.RESEND_API_KEY = "test-key"

    def boom(*args, **kwargs):
        raise urllib.error.URLError("no route to host")

    with patch.object(module.urllib.request, "urlopen", boom):
        health = module.mail_health(force=True)

    assert health["checked"] is False
    assert health["blocked"] == []
    assert "Resend" in health["reason"]


def test_no_key_is_reported_as_not_checked_rather_than_healthy(settings):
    settings.RESEND_API_KEY = ""
    health = module.mail_health(force=True)

    assert health["checked"] is False
    assert health["blocked"] == []


def test_the_result_is_cached_so_every_page_load_is_not_an_https_call(settings):
    settings.RESEND_API_KEY = "test-key"
    settings.DEFAULT_FROM_EMAIL = "info@genmars.co.ke"

    calls = []

    def counting(*args, **kwargs):
        calls.append(1)
        return _Response({"data": []})

    with patch.object(module.urllib.request, "urlopen", counting):
        module.mail_health(force=True)
        module.mail_health()
        module.mail_health()

    assert len(calls) == 1


def test_the_operations_overview_carries_it(client, staff, settings):
    """
    It rides on the overview because that is the screen everyone here loads,
    and the failure it reports cannot be reported by email.
    """
    settings.RESEND_API_KEY = "test-key"
    settings.DEFAULT_FROM_EMAIL = "info@genmars.co.ke"

    payload = {"data": [{
        "email": "info@genmars.co.ke", "origin": "bounce",
        "created_at": "2026-09-01 12:43:01+00",
    }]}

    client.force_login(staff)
    with _resend(payload):
        body = client.get(reverse("ops-overview")).json()

    assert body["mail"]["checked"] is True
    assert body["mail"]["blocked"][0]["email"] == "info@genmars.co.ke"
