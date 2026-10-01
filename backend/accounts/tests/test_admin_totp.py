"""
The second factor on the Django admin.

═══════════════════════════════════════════════════════════════════════════════
WHY THIS EXISTS.

/admin/ is reachable from anywhere on the internet and an account that can open
it reads every client's data. Until this, the only thing between a stolen
password and all of it was the password.

These tests are written against the HTTP surface an attacker actually has, the
same way test_admin_login.py is: "the form class is installed" is not the claim
worth defending. The claim worth defending is "a correct password alone does
not get in".
═══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import base64
import time

import pytest
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from accounts import totp
from accounts.models import StaffTotp, User

pytestmark = pytest.mark.django_db

PASSWORD = "correct-horse-battery"
EMAIL = "founder@genmars.co.ke"


@pytest.fixture(autouse=True)
def _clear_rate_limit():
    # The admin login is limited to 10 POSTs a minute per address, and these
    # tests make more than that between them.
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def founder():
    return User.objects.create_user(
        email=EMAIL, password=PASSWORD, is_staff=True, is_superuser=True
    )


def enrol(user) -> StaffTotp:
    return StaffTotp.objects.create(
        user=user,
        secret=totp.new_secret(),
        confirmed_at=timezone.now(),
    )


def sign_in(client, **extra):
    return client.post(
        reverse("admin:login"),
        {"username": EMAIL, "password": PASSWORD, "next": "/admin/", **extra},
    )


def signed_in(response) -> bool:
    """A successful admin login redirects; a refusal re-renders the form."""
    return response.status_code == 302


# ── the algorithm ───────────────────────────────────────────────────────────


def test_the_rfc_6238_vectors_match():
    """
    ── THE ONLY PROOF THAT MATTERS FOR A CRYPTO PRIMITIVE ──────────────────
    Hand-rolled TOTP is defensible precisely because the answer is published.
    These are RFC 6238 Appendix B, SHA-1, truncated to the six digits this
    implementation emits. If any of them drifts, the thing is wrong however
    well the rest of the suite passes.
    """
    secret = base64.b32encode(b"12345678901234567890").decode()
    for unix_time, expected in [
        (59, "287082"),
        (1111111109, "081804"),
        (1111111111, "050471"),
        (1234567890, "005924"),
        (2000000000, "279037"),
        (20000000000, "353130"),
    ]:
        assert totp._code_at(secret, unix_time // 30) == expected


def test_a_code_is_accepted_within_the_skew_window():
    secret = totp.new_secret()
    now = time.time()
    for offset in (-30, 0, 30):
        assert totp.verify(secret, totp._code_at(secret, totp.current_step(now + offset)),
                           at=now) is not None


def test_a_code_from_too_long_ago_is_refused():
    secret = totp.new_secret()
    now = time.time()
    stale = totp._code_at(secret, totp.current_step(now) - 5)
    assert totp.verify(secret, stale, at=now) is None


def test_rubbish_is_refused_without_raising():
    secret = totp.new_secret()
    for junk in ("", "abcdef", "12345", "1234567", None, "  "):
        assert totp.verify(secret, junk) is None


# ── the login ───────────────────────────────────────────────────────────────


def test_the_right_password_alone_is_not_enough(client, founder):
    """
    ══════════════════════════════════════════════════════════════════════
    THE ONE THAT MATTERS. Everything else is detail.
    ══════════════════════════════════════════════════════════════════════
    """
    enrol(founder)
    assert not signed_in(sign_in(client))


def test_the_password_and_a_code_get_in(client, founder):
    """The control. The test above has to fail for the right reason."""
    device = enrol(founder)
    code = totp._code_at(device.secret, totp.current_step())
    assert signed_in(sign_in(client, token=code))


def test_a_wrong_code_says_what_a_wrong_password_says(client, founder):
    """
    "Password right, code wrong" tells an attacker they have found a working
    password and should go and get the phone. The uniform message tells them
    nothing they did not already know — the same reasoning identity.py
    applies to "no such account" versus "wrong password".
    """
    enrol(founder)
    with_bad_code = sign_in(client, token="000000")
    with_bad_password = client.post(
        reverse("admin:login"),
        {"username": EMAIL, "password": "wrong", "next": "/admin/"},
    )
    assert not signed_in(with_bad_code)
    assert not signed_in(with_bad_password)

    def errors(response):
        return response.context_data["form"].errors.get("__all__", [])

    assert errors(with_bad_code) == errors(with_bad_password)


def test_a_code_cannot_be_used_twice(client, founder):
    """
    Without this a code is good for its whole thirty seconds, and anybody
    who reads it over a shoulder — or phishes it beside the password — gets
    in behind the person who typed it.
    """
    device = enrol(founder)
    code = totp._code_at(device.secret, totp.current_step())

    assert signed_in(sign_in(client, token=code))
    client.logout()
    assert not signed_in(sign_in(client, token=code))


def test_a_locked_account_is_refused_before_the_code_is_looked_at(client, founder):
    """
    The password check runs first, so the lockout in IdentityBackend still
    counts wrong passwords. A second factor that let somebody probe passwords
    without spending the lockout would make the first factor weaker.
    """
    device = enrol(founder)
    founder.locked_until = timezone.now() + timezone.timedelta(minutes=30)
    founder.save(update_fields=["locked_until"])

    code = totp._code_at(device.secret, totp.current_step())
    assert not signed_in(sign_in(client, token=code))


# ── the rollout ─────────────────────────────────────────────────────────────


def test_somebody_with_no_authenticator_still_gets_in_by_default(client, founder):
    """
    ══════════════════════════════════════════════════════════════════════
    THE DEPLOY-DAY TEST.

    Nobody has a device until somebody runs enrol_totp, that needs a shell,
    and demanding one on the deploy locks out the person who would run it.
    This must stay true until ADMIN_REQUIRE_TOTP is deliberately turned on.
    ══════════════════════════════════════════════════════════════════════
    """
    assert signed_in(sign_in(client))


@override_settings(ADMIN_REQUIRE_TOTP=True)
def test_with_the_setting_on_no_authenticator_means_no_admin(client, founder):
    assert not signed_in(sign_in(client))


def test_an_unconfirmed_enrolment_enforces_nothing(client, founder):
    """
    A half-finished enrolment must not be able to lock somebody out — the
    command writes nothing until a code comes back, but a row could also
    arrive from a migration or a shell.
    """
    StaffTotp.objects.create(user=founder, secret=totp.new_secret())
    assert signed_in(sign_in(client))
