"""
The Django admin login obeys the same rules as the API sign-in.

═══════════════════════════════════════════════════════════════════════════════
WHY THIS FILE EXISTS.

Until 2026-09-09 it did not, and the admin login enforced neither the lockout
nor any throttle: AUTHENTICATION_BACKENDS was unset, so Django used the stock
ModelBackend, which compares a hash and knows nothing about User.locked_until.
The API sign-in endpoint was hardened and /admin/login/ was not, on the same
accounts, with the same passwords — and admin accounts are staff and
superusers, which read across every organisation.

These tests are written against the HTTP surface an attacker actually has, not
against the backend class, because "the backend is configured" is not the claim
worth defending. The claim worth defending is "six wrong passwords at
/admin/login/ stop working".
═══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import importlib
import os
import re

import pytest
from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ImproperlyConfigured
from django.urls import reverse

from accounts import identity
from accounts.auth_backends import ADMIN_LOGIN_RATE, SESSION_BACKEND
from accounts.models import User

pytestmark = pytest.mark.django_db

PASSWORD = "correct-horse-battery"
EMAIL = "founder@genmars.co.ke"

ADMIN_LOGIN = "/admin/login/"
ADMIN_LOGIN_ATTEMPTS = int(ADMIN_LOGIN_RATE.split("/")[0])


@pytest.fixture(autouse=True)
def _clean_rate_limit_counters():
    """
    The rate limit counts in the CACHE, which is not reset between tests the
    way the database is.

    Without this, tests leak into each other: the enumeration test alone makes
    eight POSTs, so whichever test happened to run fourth would start failing
    with a 403 that has nothing to do with what it is asserting. That is a
    worse failure than a real one, because it moves depending on test order.
    """
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def staff() -> User:
    user = identity.create_account(EMAIL, PASSWORD, "A Founder")
    user.is_staff = True
    user.is_superuser = True
    user.staff_role = "founder"
    user.save(update_fields=["is_staff", "is_superuser", "staff_role"])
    return user


def _attempt(client, email: str, password: str):
    return client.post(
        ADMIN_LOGIN,
        {"username": email, "password": password, "next": "/admin/"},
    )


# ── the finding itself ──────────────────────────────────────────────────────


def test_admin_login_locks_out_after_repeated_failures(client, staff):
    """
    The whole point of the file. Five wrong passwords lock the account, and the
    sixth attempt fails even though it is the CORRECT password.

    Before the IdentityBackend, the sixth attempt signed the attacker in.
    """
    for _ in range(identity.MAX_FAILED_SIGN_INS):
        _attempt(client, EMAIL, "wrong-password")

    staff.refresh_from_db()
    assert staff.is_locked, "five failures at /admin/login/ must lock the account"

    response = _attempt(client, EMAIL, PASSWORD)
    assert response.status_code == 200, "a locked account must not be redirected in"
    assert "_auth_user_id" not in client.session


def test_the_lockout_is_the_same_counter_the_api_uses(client, staff):
    """
    Not two counters that happen to agree. Failures at the admin door count
    towards a lockout the API then honours, and vice versa — otherwise an
    attacker gets five free attempts per surface.
    """
    for _ in range(identity.MAX_FAILED_SIGN_INS - 1):
        _attempt(client, EMAIL, "wrong-password")

    staff.refresh_from_db()
    assert staff.failed_sign_ins == identity.MAX_FAILED_SIGN_INS - 1

    # The last failure comes through the API, and it is what tips the lock.
    with pytest.raises(identity.AuthError):
        identity.authenticate(EMAIL, "wrong-password")

    staff.refresh_from_db()
    assert staff.is_locked


def test_a_correct_password_still_signs_in(client, staff):
    """The lock must not be bought at the cost of the door working."""
    response = _attempt(client, EMAIL, PASSWORD)
    assert response.status_code == 302
    assert client.session["_auth_user_id"] == str(staff.pk)


def test_a_successful_sign_in_clears_the_counter(client, staff):
    for _ in range(3):
        _attempt(client, EMAIL, "wrong-password")
    _attempt(client, EMAIL, PASSWORD)

    staff.refresh_from_db()
    assert staff.failed_sign_ins == 0
    assert staff.locked_until is None


def test_a_deactivated_account_cannot_use_the_admin(client, staff):
    staff.is_active = False
    staff.save(update_fields=["is_active"])

    response = _attempt(client, EMAIL, PASSWORD)
    assert response.status_code == 200
    assert "_auth_user_id" not in client.session


# ── no enumeration oracle at the login form ─────────────────────────────────


def test_the_admin_refusal_does_not_reveal_which_accounts_exist(client, staff):
    """
    A locked real account, a wrong password on a real account, and an address
    that was never registered must all look the same to whoever is guessing.
    Anything else confirms an address is real, which is the enumeration oracle
    the API sign-in already refuses to be.
    """
    unknown = _attempt(client, "nobody@example.invalid", PASSWORD).content

    wrong = _attempt(client, EMAIL, "wrong-password").content

    for _ in range(identity.MAX_FAILED_SIGN_INS):
        _attempt(client, EMAIL, "wrong-password")
    locked = _attempt(client, EMAIL, PASSWORD).content

    # Just the rendered error sentence. Comparing pages, or a slice of one,
    # compares the CSRF token too — which is different every time and would
    # make this pass for the wrong reason if the assertion were ever inverted.
    def message(body: bytes) -> str:
        found = re.search(
            r'class="errornote"[^>]*>(.*?)</p>', body.decode(), re.DOTALL
        )
        assert found, "expected Django's errornote on a refused admin login"
        return " ".join(found.group(1).split())

    assert message(unknown) == message(wrong) == message(locked)
    # And it must actually say something, so a future template change that
    # empties the note cannot make three empty strings look like agreement.
    assert len(message(unknown)) > 20


# ── the wiring that makes all of the above true ─────────────────────────────


def test_only_the_identity_backend_is_configured():
    """
    A second backend would undo this silently. Django tries each in turn and
    takes the first that returns a user, so listing ModelBackend after ours
    would wave through exactly the locked accounts the first one refused.
    """
    assert settings.AUTHENTICATION_BACKENDS == [SESSION_BACKEND]


def test_session_backend_path_matches_settings():
    """
    login() stamps SESSION_BACKEND into the session and every later request
    loads that class to resolve the session. If the constant and the setting
    drift, nothing raises — sessions just read as signed out, for everybody.

    That is not hypothetical: five views hardcoded the old ModelBackend path,
    and switching the setting signed the entire test suite out at once.
    """
    assert SESSION_BACKEND in settings.AUTHENTICATION_BACKENDS


def test_no_view_hardcodes_a_backend_path():
    """
    The reason the constant exists. A literal backend path in a view is a
    silent sign-out waiting for the next time this setting changes.
    """
    from pathlib import Path

    views = Path(__file__).resolve().parent.parent / "views.py"
    assert "backends.ModelBackend" not in views.read_text()


# ── the per-IP limit ────────────────────────────────────────────────────────


def test_the_admin_login_is_rate_limited_per_address(client, staff):
    """
    The lockout is per ACCOUNT and does not cover password spraying: one
    password tried once each against forty staff addresses never trips a
    five-attempt counter, because no counter reaches two.

    So the address doing the trying is limited too. This walks past the limit
    with a DIFFERENT email every time, so no account lockout can be what stops
    it — only the per-IP rule can.
    """
    blocked = None
    for attempt in range(ADMIN_LOGIN_ATTEMPTS + 5):
        response = _attempt(client, f"spray-{attempt}@example.invalid", "Summer2026!")
        if response.status_code == 403:
            blocked = attempt
            break

    assert blocked is not None, "spraying distinct addresses was never rate limited"
    assert blocked <= ADMIN_LOGIN_ATTEMPTS + 1, (
        f"limit is {ADMIN_LOGIN_ATTEMPTS}/min but {blocked} attempts got through"
    )


def test_loading_the_login_page_is_never_rate_limited(client):
    """
    Only POST is counted. The admin redirects here from every deep link, so a
    founder clicking a bookmark while signed out must not spend the budget that
    exists to stop guessing — and must never be locked out of the FORM.
    """
    for _ in range(ADMIN_LOGIN_ATTEMPTS * 3):
        assert client.get(ADMIN_LOGIN).status_code == 200


def test_production_refuses_to_boot_on_an_unshared_cache():
    """
    The limit counts in the default cache. Per-process local memory means every
    gunicorn worker keeps its own counter, so the real limit is N times what it
    says and a redeploy resets them all — a control that reads as on while
    being worth a fraction of what it claims.

    The TEST settings deliberately use locmem: one process, no workers, and
    needing a live Redis to run the suite would be a worse trade. So this boots
    the PRODUCTION settings module for real, with REDIS_URL absent, and asserts
    it refuses — rather than asserting the value under the test settings, where
    locmem is correct.
    """
    previous = dict(os.environ)
    os.environ.update(
        {
            "DEBUG": "False",
            "DJANGO_SECRET_KEY": "test-only-not-a-real-secret",
            "ALLOWED_HOSTS": "api.genmars.co.ke",
            "CSRF_TRUSTED_ORIGINS": "https://api.genmars.co.ke",
            "EMAIL_BACKEND": "accounts.mail_backends.ResendBackend",
            "RESEND_API_KEY": "re_test_only",
        }
    )
    os.environ.pop("REDIS_URL", None)
    try:
        import config.settings

        with pytest.raises(ImproperlyConfigured, match="REDIS_URL"):
            importlib.reload(config.settings)
    finally:
        os.environ.clear()
        os.environ.update(previous)
        # Leave the module as the rest of the session expects to find it.
        import config.settings

        importlib.reload(config.settings)


def test_the_limit_counts_per_caller_not_per_proxy(client, staff):
    """
    The defect this was shipped with on 2026-09-09, stated as behaviour.

    django-ratelimit's key="ip" reads REMOTE_ADDR, which behind Caddy is the
    Docker gateway for every request on the internet. The limit was therefore
    one shared budget: it still stopped spraying, but one attacker could hold
    it open and deny the founder the admin, and the code called it "per-IP".

    Exhausting one address must not spend another's. This fails without
    RATELIMIT_IP_META_KEY, because both clients below look identical to it.
    """
    attacker = {"HTTP_X_REAL_IP": "203.0.113.10"}
    founder = {"HTTP_X_REAL_IP": "198.51.100.20"}

    last = None
    for _ in range(ADMIN_LOGIN_ATTEMPTS + 2):
        last = client.post(
            ADMIN_LOGIN,
            {"username": "spray@example.invalid", "password": "x"},
            **attacker,
        )
    assert last.status_code == 403, "the attacker's own budget must run out"

    # The founder, from a different address, is unaffected.
    mine = client.post(
        ADMIN_LOGIN,
        {"username": EMAIL, "password": PASSWORD, "next": "/admin/"},
        **founder,
    )
    assert mine.status_code != 403, (
        "one address exhausting the limit locked out a different address — "
        "the limit is counting the proxy, not the caller"
    )


def test_a_missing_forwarded_header_does_not_500(client, staff):
    """
    RATELIMIT_IP_META_KEY set to a bare header name raises ImproperlyConfigured
    when the header is absent, which is a 500 on the login form. The callable
    falls back to REMOTE_ADDR instead — this is the test client, which sends no
    X-Real-IP at all.
    """
    response = _attempt(client, EMAIL, "wrong-password")
    assert response.status_code == 200
