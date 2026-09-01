"""
Auth API tests.

The identity tests cover the logic; these cover the HTTP surface, where the
mistakes are different: leaking through a status code, forgetting to sign
someone in, returning a code in a response body, or letting an unauthenticated
request reach something it should not.
"""

from __future__ import annotations

import re

import pytest
from django.core import mail
from django.urls import reverse
from django.utils import timezone

from accounts import identity
from accounts.models import EmailCode, User

pytestmark = pytest.mark.django_db

PASSWORD = "correct-horse-battery"
EMAIL = "client@example.com"


@pytest.fixture(autouse=True)
def _clear_throttles():
    """
    Throttle state lives in the cache and would otherwise leak between tests —
    one test's requests exhausting a limit for the next.
    """
    from django.core.cache import cache

    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def user() -> User:
    u = User.objects.create_user(email=EMAIL, password=PASSWORD, full_name="A Client")
    identity.verify_email(u, identity.issue_code(u, EmailCode.Purpose.VERIFY).code)
    u.refresh_from_db()
    return u


def code_from_last_email() -> str:
    """The only place a code is legitimately readable: the email we sent."""
    match = re.search(r"\b(\d{6})\b", mail.outbox[-1].body)
    assert match, "no six-digit code in the email body"
    return match.group(1)


# ─────────────────────────────────────────────────────────────────────────────
# Sign in
# ─────────────────────────────────────────────────────────────────────────────


def test_sign_in_succeeds_and_starts_a_session(client, user):
    r = client.post(
        reverse("sign-in"),
        {"email": EMAIL, "password": PASSWORD},
        content_type="application/json",
    )
    assert r.status_code == 200
    # Verified but with no organisation yet, so onboarding — not the dashboard.
    assert r.json()["next"] == "/onboarding"
    # The session must actually be authenticated, not merely a 200.
    assert client.session.get("_auth_user_id") == str(user.pk)


def test_sign_in_with_wrong_password_is_401(client, user):
    r = client.post(
        reverse("sign-in"),
        {"email": EMAIL, "password": "wrong"},
        content_type="application/json",
    )
    assert r.status_code == 401
    assert "_auth_user_id" not in client.session


def test_unknown_email_is_indistinguishable_from_wrong_password(client, user):
    """
    Status AND body must match. A different status code is just as good an
    enumeration oracle as a different message.
    """
    unknown = client.post(
        reverse("sign-in"),
        {"email": "nobody@example.com", "password": PASSWORD},
        content_type="application/json",
    )
    wrong = client.post(
        reverse("sign-in"),
        {"email": EMAIL, "password": "wrong"},
        content_type="application/json",
    )
    assert unknown.status_code == wrong.status_code
    assert unknown.json() == wrong.json()


def test_locked_account_returns_423(client, user):
    for _ in range(identity.MAX_FAILED_SIGN_INS):
        client.post(
            reverse("sign-in"),
            {"email": EMAIL, "password": "wrong"},
            content_type="application/json",
        )
    r = client.post(
        reverse("sign-in"),
        {"email": EMAIL, "password": PASSWORD},
        content_type="application/json",
    )
    assert r.status_code == 423


def test_unverified_account_is_sent_to_verify_not_the_dashboard(client):
    User.objects.create_user(email="new@example.com", password=PASSWORD)
    r = client.post(
        reverse("sign-in"),
        {"email": "new@example.com", "password": PASSWORD},
        content_type="application/json",
    )
    # The address is part of the destination — /verify posts it back, and a
    # bare "/verify" left the screen unable to verify or resend anything.
    assert r.json()["next"] == "/verify?email=new%40example.com"


# ─────────────────────────────────────────────────────────────────────────────
# Sign up
# ─────────────────────────────────────────────────────────────────────────────


def test_sign_up_creates_an_account_and_sends_a_code(client):
    r = client.post(
        reverse("sign-up"),
        {"email": "new@example.com", "password": PASSWORD, "full_name": "New"},
        content_type="application/json",
    )
    assert r.status_code == 200
    assert User.objects.filter(email="new@example.com").exists()
    assert len(mail.outbox) == 1
    assert code_from_last_email()


def test_sign_up_never_returns_the_code(client):
    """A code in a response body is a code in every proxy log between us."""
    r = client.post(
        reverse("sign-up"),
        {"email": "new@example.com", "password": PASSWORD},
        content_type="application/json",
    )
    assert not re.search(r"\b\d{6}\b", r.content.decode())


def test_sign_up_with_an_existing_address_does_not_reveal_it(client, user):
    """
    Registering an address that already exists must look exactly like a fresh
    signup, or the form becomes a membership check.
    """
    fresh = client.post(
        reverse("sign-up"),
        {"email": "brand-new@example.com", "password": PASSWORD},
        content_type="application/json",
    )
    taken = client.post(
        reverse("sign-up"),
        {"email": EMAIL, "password": PASSWORD},
        content_type="application/json",
    )
    assert fresh.status_code == taken.status_code == 200
    assert taken.json()["next"].startswith("/verify")


def test_weak_password_is_rejected(client):
    r = client.post(
        reverse("sign-up"),
        {"email": "new@example.com", "password": "password12"},
        content_type="application/json",
    )
    assert r.status_code == 400


def test_short_password_is_rejected(client):
    r = client.post(
        reverse("sign-up"),
        {"email": "new@example.com", "password": "short"},
        content_type="application/json",
    )
    assert r.status_code == 400


# ─────────────────────────────────────────────────────────────────────────────
# Verify
# ─────────────────────────────────────────────────────────────────────────────


def test_verify_round_trip(client):
    client.post(
        reverse("sign-up"),
        {"email": "new@example.com", "password": PASSWORD},
        content_type="application/json",
    )
    r = client.post(
        reverse("verify"),
        {"email": "new@example.com", "code": code_from_last_email()},
        content_type="application/json",
    )
    assert r.status_code == 200
    # Verified, but this account still has no organisation — onboarding next.
    assert r.json()["next"] == "/onboarding"
    assert User.objects.get(email="new@example.com").is_email_verified


def test_verify_with_unknown_email_matches_a_bad_code(client, user):
    unknown = client.post(
        reverse("verify"),
        {"email": "nobody@example.com", "code": "000000"},
        content_type="application/json",
    )
    bad = client.post(
        reverse("verify"),
        {"email": EMAIL, "code": "000000"},
        content_type="application/json",
    )
    assert unknown.status_code == bad.status_code == 400
    assert unknown.json() == bad.json()


def test_non_numeric_code_is_rejected_by_validation(client, user):
    r = client.post(
        reverse("verify"),
        {"email": EMAIL, "code": "abcdef"},
        content_type="application/json",
    )
    assert r.status_code == 400


# ─────────────────────────────────────────────────────────────────────────────
# Forgot / reset
# ─────────────────────────────────────────────────────────────────────────────


def test_forgot_reports_success_for_an_unknown_address(client):
    r = client.post(
        reverse("forgot"),
        {"email": "nobody@example.com"},
        content_type="application/json",
    )
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    assert len(mail.outbox) == 0, "must not send mail for an address we do not have"


def test_forgot_and_reset_round_trip(client, user):
    client.post(reverse("forgot"), {"email": EMAIL}, content_type="application/json")
    r = client.post(
        reverse("reset"),
        {"email": EMAIL, "code": code_from_last_email(), "password": "a-new-long-password"},
        content_type="application/json",
    )
    assert r.status_code == 200
    assert client.session.get("_auth_user_id") == str(user.pk)
    user.refresh_from_db()
    assert user.check_password("a-new-long-password")


def test_reset_code_cannot_be_replayed(client, user):
    client.post(reverse("forgot"), {"email": EMAIL}, content_type="application/json")
    code = code_from_last_email()
    client.post(
        reverse("reset"),
        {"email": EMAIL, "code": code, "password": "first-new-password"},
        content_type="application/json",
    )
    r = client.post(
        reverse("reset"),
        {"email": EMAIL, "code": code, "password": "attacker-password"},
        content_type="application/json",
    )
    assert r.status_code == 400
    user.refresh_from_db()
    assert user.check_password("first-new-password")


# ─────────────────────────────────────────────────────────────────────────────
# Session
# ─────────────────────────────────────────────────────────────────────────────


def test_session_reports_anonymous_and_sets_the_csrf_cookie(client):
    r = client.get(reverse("session"))
    assert r.status_code == 200
    assert r.json() == {"authenticated": False}
    assert "gm_csrftoken" in r.cookies, "the frontend cannot POST without this"


def test_session_reports_the_signed_in_user(client, user):
    client.post(
        reverse("sign-in"),
        {"email": EMAIL, "password": PASSWORD},
        content_type="application/json",
    )
    body = client.get(reverse("session")).json()
    assert body["authenticated"] is True
    assert body["email"] == EMAIL
    assert "password" not in body


def test_sign_out_clears_the_session(client, user):
    client.post(
        reverse("sign-in"),
        {"email": EMAIL, "password": PASSWORD},
        content_type="application/json",
    )
    assert client.delete(reverse("session")).status_code == 204
    assert "_auth_user_id" not in client.session


def test_change_password_requires_authentication(client):
    r = client.post(
        reverse("change-password"),
        {"current_password": "x", "new_password": "a-new-long-password"},
        content_type="application/json",
    )
    assert r.status_code in (401, 403)


def test_change_password_keeps_the_session_alive(client, user):
    client.post(
        reverse("sign-in"),
        {"email": EMAIL, "password": PASSWORD},
        content_type="application/json",
    )
    r = client.post(
        reverse("change-password"),
        {"current_password": PASSWORD, "new_password": "a-new-long-password"},
        content_type="application/json",
    )
    assert r.status_code == 200
    # Changing a password must not log you out of the tab you are sitting in.
    assert client.session.get("_auth_user_id") == str(user.pk)


# ─────────────────────────────────────────────────────────────────────────────
# Throttling
# ─────────────────────────────────────────────────────────────────────────────


def test_sign_in_is_rate_limited(client, user):
    """
    The per-account lock does nothing against one password sprayed across many
    accounts. The per-IP limit is what covers that.
    """
    seen = set()
    for i in range(14):
        r = client.post(
            reverse("sign-in"),
            {"email": f"user{i}@example.com", "password": "wrong"},
            content_type="application/json",
        )
        seen.add(r.status_code)
    assert 429 in seen, "sign-in must be throttled per client address"


def test_code_requests_are_rate_limited(client, user):
    """Each request sends an email; unthrottled, this mail-bombs a stranger."""
    seen = set()
    for _ in range(10):
        r = client.post(
            reverse("forgot"), {"email": EMAIL}, content_type="application/json"
        )
        seen.add(r.status_code)
    assert 429 in seen


# ─────────────────────────────────────────────────────────────────────────────
# Sign-in on an UNVERIFIED account
# ─────────────────────────────────────────────────────────────────────────────
#
# This path shipped broken: it routed to /verify without an address and without
# sending anything, so the screen asked for a code that had never been sent and
# could not be resent. Both halves are asserted here.


@pytest.mark.django_db
def test_sign_in_unverified_sends_a_code_and_names_the_address(client, mailoutbox):
    identity.create_account("unverified@example.com", "correct-horse-battery", "U")

    res = client.post(
        "/api/auth/sign-in",
        {"email": "unverified@example.com", "password": "correct-horse-battery"},
        content_type="application/json",
    )

    assert res.status_code == 200
    # The address must ride along, or /verify has nothing to post back.
    assert res.json()["next"] == "/verify?email=unverified%40example.com"
    # And a code must actually have been sent.
    assert len(mailoutbox) == 1
    assert "unverified@example.com" in mailoutbox[0].to


@pytest.mark.django_db
def test_sign_in_verified_goes_straight_to_the_dashboard(client, mailoutbox):
    user = identity.create_account("verified@example.com", "correct-horse-battery", "V")
    user.email_verified_at = timezone.now()
    user.save()

    res = client.post(
        "/api/auth/sign-in",
        {"email": "verified@example.com", "password": "correct-horse-battery"},
        content_type="application/json",
    )

    # Verified, but onboarding is not finished, so not the dashboard yet.
    assert res.json()["next"] == "/onboarding"
    # No code for someone who does not need one.
    assert mailoutbox == []


@pytest.mark.django_db
def test_verify_url_quotes_a_plus_in_the_local_part(client):
    """A "+" is legal in an email and decodes to a space if it is not quoted."""
    identity.create_account("user+tag@example.com", "correct-horse-battery", "P")

    res = client.post(
        "/api/auth/sign-in",
        {"email": "user+tag@example.com", "password": "correct-horse-battery"},
        content_type="application/json",
    )

    assert res.json()["next"] == "/verify?email=user%2Btag%40example.com"


# ─────────────────────────────────────────────────────────────────────────────
# Routing: the three states an account can be in
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_sign_in_reaches_the_dashboard_once_onboarding_is_done(client):
    user = identity.create_account("done@example.com", PASSWORD, "Done")
    user.email_verified_at = timezone.now()
    user.save()
    identity.attach_organisation(user, "Sarova Logistics")

    res = client.post(
        reverse("sign-in"),
        {"email": "done@example.com", "password": PASSWORD},
        content_type="application/json",
    )

    assert res.json()["next"] == "/dashboard"


@pytest.mark.django_db
def test_staff_never_get_sent_to_onboarding(client):
    """
    Staff have no membership and never will — they work in the admin. Routing
    them by the client rule would loop them through a form they cannot finish.
    """
    user = identity.create_account("team@genmars.co.ke", PASSWORD, "Team")
    user.email_verified_at = timezone.now()
    user.is_staff = True
    user.save()

    res = client.post(
        reverse("sign-in"),
        {"email": "team@genmars.co.ke", "password": PASSWORD},
        content_type="application/json",
    )

    assert res.json()["next"] == "/dashboard"


@pytest.mark.django_db
def test_session_reports_needs_onboarding(client):
    user = identity.create_account("fresh@example.com", PASSWORD, "Fresh")
    user.email_verified_at = timezone.now()
    user.save()
    client.force_login(user)

    assert client.get(reverse("session")).json()["needs_onboarding"] is True

    identity.attach_organisation(user, "Fresh Ltd")
    assert client.get(reverse("session")).json()["needs_onboarding"] is False


@pytest.mark.django_db
def test_verifying_an_email_leads_to_onboarding_not_the_dashboard(client):
    """
    Verification is the step BEFORE onboarding. This returned a hard-coded
    /dashboard, which sent a brand-new client to an empty dashboard with a form
    still unfilled behind them.
    """
    user = identity.create_account("brand@example.com", PASSWORD, "Brand New")
    issued = identity.issue_code(user, EmailCode.Purpose.VERIFY)

    res = client.post(
        reverse("verify"),
        {"email": "brand@example.com", "code": issued.code},
        content_type="application/json",
    )

    assert res.json()["next"] == "/onboarding"


# ─────────────────────────────────────────────────────────────────────────────
# Address case
#
# Found in production on 2026-09-01: a real signup could not be completed. The
# account existed, the code had been emailed and delivered, and the address was
# retyped on the verify screen with one capital letter. Four views looked the
# user up with the raw submitted value while everything else in the system
# stores and queries lower-cased, so nothing matched.
#
# The failures were invisible on purpose, which is what made it expensive:
# verify cannot say "no such address" without handing out an enumeration
# oracle, and request-code cannot say it either. So the visitor saw "that code
# is not right, or it has expired" against a code that was neither, asked for
# another, was told one had been sent, and none had.
# ─────────────────────────────────────────────────────────────────────────────


def test_verify_accepts_the_address_in_any_case(client):
    client.post(
        reverse("sign-up"),
        {"email": "new@example.com", "password": PASSWORD},
        content_type="application/json",
    )
    r = client.post(
        reverse("verify"),
        {"email": "New@Example.COM", "code": code_from_last_email()},
        content_type="application/json",
    )
    assert r.status_code == 200, r.json()
    assert User.objects.get(email="new@example.com").is_email_verified


def test_verify_tolerates_surrounding_whitespace(client):
    """Pasted addresses arrive with a trailing space more often than anyone expects."""
    client.post(
        reverse("sign-up"),
        {"email": "spaced@example.com", "password": PASSWORD},
        content_type="application/json",
    )
    r = client.post(
        reverse("verify"),
        {"email": "  spaced@example.com  ", "code": code_from_last_email()},
        content_type="application/json",
    )
    assert r.status_code == 200, r.json()


def test_resending_a_code_works_in_any_case(client):
    client.post(
        reverse("sign-up"),
        {"email": "resend@example.com", "password": PASSWORD},
        content_type="application/json",
    )
    before = len(mail.outbox)

    r = client.post(
        reverse("request-code"),
        {"email": "ReSend@Example.com"},
        content_type="application/json",
    )

    # 200 either way — the response cannot reveal whether the address is known.
    # So the assertion has to be that an email was actually SENT, which is the
    # thing that was silently not happening.
    assert r.status_code == 200
    assert len(mail.outbox) == before + 1
    assert mail.outbox[-1].to == ["resend@example.com"]


def test_password_reset_starts_in_any_case(client, user):
    before = len(mail.outbox)
    r = client.post(
        reverse("forgot"),
        {"email": EMAIL.upper()},
        content_type="application/json",
    )
    assert r.status_code == 200
    assert len(mail.outbox) == before + 1
    assert mail.outbox[-1].to == [EMAIL]


def test_signing_up_again_in_a_different_case_does_not_make_a_second_account(client):
    """
    The address is unique and stored lower-cased, so a second signup differing
    only in case must be treated as the existing account — a code to the owner,
    and no hint to a prober that the address is taken.
    """
    for address in ("dup@example.com", "DUP@Example.com"):
        client.post(
            reverse("sign-up"),
            {"email": address, "password": PASSWORD},
            content_type="application/json",
        )
    assert User.objects.filter(email="dup@example.com").count() == 1
    assert User.objects.count() == 1
