"""
Tests for the identity boundary.

These are the tests that matter. Every case here is one where a bug is a
security incident rather than a cosmetic defect, so they are written against
behaviour a client or an attacker can observe — not against implementation.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from accounts import identity
from accounts.models import EmailCode, User

pytestmark = pytest.mark.django_db

PASSWORD = "correct-horse-battery"
EMAIL = "client@example.com"


@pytest.fixture
def user() -> User:
    return User.objects.create_user(email=EMAIL, password=PASSWORD, full_name="A Client")


# ─────────────────────────────────────────────────────────────────────────────
# Sign in
# ─────────────────────────────────────────────────────────────────────────────


def test_correct_credentials_authenticate(user):
    assert identity.authenticate(EMAIL, PASSWORD) == user


def test_email_is_case_insensitive(user):
    assert identity.authenticate("CLIENT@EXAMPLE.COM", PASSWORD) == user


def test_wrong_password_is_rejected(user):
    with pytest.raises(identity.AuthError) as e:
        identity.authenticate(EMAIL, "not-the-password")
    assert e.value.reason == "bad_password"


def test_unknown_email_is_rejected():
    with pytest.raises(identity.AuthError) as e:
        identity.authenticate("nobody@example.com", PASSWORD)
    assert e.value.reason == "unknown_email"


def test_unknown_email_and_wrong_password_are_indistinguishable(user):
    """
    Account enumeration. The message a caller may show MUST be identical, or the
    sign-in form becomes a free "is this address registered?" oracle.
    """
    with pytest.raises(identity.AuthError) as unknown:
        identity.authenticate("nobody@example.com", PASSWORD)
    with pytest.raises(identity.AuthError) as wrong:
        identity.authenticate(EMAIL, "not-the-password")
    assert unknown.value.safe_message == wrong.value.safe_message


def test_inactive_account_cannot_sign_in(user):
    user.is_active = False
    user.save(update_fields=["is_active"])
    with pytest.raises(identity.AuthError) as e:
        identity.authenticate(EMAIL, PASSWORD)
    assert e.value.reason == "inactive_account"


# ─────────────────────────────────────────────────────────────────────────────
# Lockout
# ─────────────────────────────────────────────────────────────────────────────


def test_account_locks_after_repeated_failures(user):
    for _ in range(identity.MAX_FAILED_SIGN_INS - 1):
        with pytest.raises(identity.AuthError):
            identity.authenticate(EMAIL, "wrong")

    with pytest.raises(identity.AccountLocked):
        identity.authenticate(EMAIL, "wrong")

    # And the CORRECT password is refused while locked — otherwise the lockout
    # does nothing against someone who eventually guesses right.
    with pytest.raises(identity.AccountLocked):
        identity.authenticate(EMAIL, PASSWORD)


def test_lock_expires_without_intervention(user):
    """Charter 05 §III promises a response, not a support ticket to get back in."""
    user.locked_until = timezone.now() - timedelta(seconds=1)
    user.save(update_fields=["locked_until"])
    assert identity.authenticate(EMAIL, PASSWORD) == user


def test_successful_sign_in_clears_failure_count(user):
    with pytest.raises(identity.AuthError):
        identity.authenticate(EMAIL, "wrong")
    identity.authenticate(EMAIL, PASSWORD)
    user.refresh_from_db()
    assert user.failed_sign_ins == 0


# ─────────────────────────────────────────────────────────────────────────────
# Registration
# ─────────────────────────────────────────────────────────────────────────────


def test_create_account_starts_unverified():
    u = identity.create_account("new@example.com", PASSWORD, "New Client")
    assert not u.is_email_verified
    assert not u.is_staff


def test_password_is_hashed_not_stored():
    """
    Asserts the property, not the algorithm — the test settings deliberately use
    a fast hasher so the suite is not dominated by Argon2's cost. That the
    PRODUCTION hasher is Argon2 is asserted separately below.
    """
    u = identity.create_account("new@example.com", PASSWORD)
    assert PASSWORD not in u.password
    assert "$" in u.password, "should be an algorithm-prefixed hash, not plaintext"
    assert u.check_password(PASSWORD)


def test_production_settings_use_argon2():
    """
    The fast hasher above is a test-only concession. If someone ever copies it
    into the real settings, this fails.
    """
    from config import settings as production

    assert production.PASSWORD_HASHERS[0].endswith("Argon2PasswordHasher")


def test_duplicate_email_is_rejected(user):
    with pytest.raises(identity.AuthError) as e:
        identity.create_account(EMAIL, PASSWORD)
    assert e.value.reason == "email_taken"


def test_create_account_does_not_create_an_order(user):
    """
    Charter 02 §I — qualification belongs to the commercial partners and the
    capacity veto to the founder. Signup must never manufacture work.
    """
    from portal.models import Order

    identity.create_account("new@example.com", PASSWORD)
    assert Order.objects.count() == 0


# ─────────────────────────────────────────────────────────────────────────────
# Codes
# ─────────────────────────────────────────────────────────────────────────────


def test_email_verification_round_trip(user):
    issued = identity.issue_code(user, EmailCode.Purpose.VERIFY)
    identity.verify_email(user, issued.code)
    user.refresh_from_db()
    assert user.is_email_verified


def test_code_is_stored_hashed(user):
    issued = identity.issue_code(user, EmailCode.Purpose.VERIFY)
    record = EmailCode.objects.get(user=user)
    assert issued.code not in record.code_hash


def test_code_cannot_be_reused(user):
    issued = identity.issue_code(user, EmailCode.Purpose.VERIFY)
    identity.redeem_code(user, EmailCode.Purpose.VERIFY, issued.code)
    with pytest.raises(identity.AuthError):
        identity.redeem_code(user, EmailCode.Purpose.VERIFY, issued.code)


def test_expired_code_is_rejected(user):
    issued = identity.issue_code(user, EmailCode.Purpose.VERIFY)
    EmailCode.objects.filter(user=user).update(
        expires_at=timezone.now() - timedelta(seconds=1)
    )
    with pytest.raises(identity.AuthError):
        identity.redeem_code(user, EmailCode.Purpose.VERIFY, issued.code)


def test_wrong_code_is_rejected(user):
    identity.issue_code(user, EmailCode.Purpose.VERIFY)
    with pytest.raises(identity.AuthError):
        identity.redeem_code(user, EmailCode.Purpose.VERIFY, "000000")


def test_code_brute_force_is_capped(user):
    """A 6-digit code must not survive unlimited guessing inside its lifetime."""
    identity.issue_code(user, EmailCode.Purpose.VERIFY)
    for _ in range(identity.MAX_CODE_ATTEMPTS):
        with pytest.raises(identity.AuthError):
            identity.redeem_code(user, EmailCode.Purpose.VERIFY, "000000")

    record = EmailCode.objects.get(user=user)
    assert record.used_at is not None, "code should be burned after too many attempts"


def test_issuing_a_new_code_invalidates_the_previous_one(user):
    first = identity.issue_code(user, EmailCode.Purpose.VERIFY)
    identity.issue_code(user, EmailCode.Purpose.VERIFY)
    with pytest.raises(identity.AuthError):
        identity.redeem_code(user, EmailCode.Purpose.VERIFY, first.code)


def test_verify_code_cannot_be_used_for_reset(user):
    """Purposes must not be interchangeable, or a verify code resets a password."""
    issued = identity.issue_code(user, EmailCode.Purpose.VERIFY)
    with pytest.raises(identity.AuthError):
        identity.redeem_code(user, EmailCode.Purpose.RESET, issued.code)


# ─────────────────────────────────────────────────────────────────────────────
# Password reset
# ─────────────────────────────────────────────────────────────────────────────


def test_password_reset_round_trip(user):
    issued = identity.start_password_reset(EMAIL)
    identity.complete_password_reset(EMAIL, issued.code, "a-brand-new-password")
    assert identity.authenticate(EMAIL, "a-brand-new-password")


def test_reset_token_cannot_be_replayed(user):
    issued = identity.start_password_reset(EMAIL)
    identity.complete_password_reset(EMAIL, issued.code, "first-new-password")
    with pytest.raises(identity.AuthError):
        identity.complete_password_reset(EMAIL, issued.code, "attacker-password")
    # The first reset must still be the one that holds.
    assert identity.authenticate(EMAIL, "first-new-password")


def test_reset_for_unknown_email_returns_none_not_an_error():
    """
    The caller responds identically either way. Raising here would leak which
    addresses are registered through the forgot-password form.
    """
    assert identity.start_password_reset("nobody@example.com") is None


def test_successful_reset_clears_a_lockout(user):
    user.locked_until = timezone.now() + timedelta(minutes=15)
    user.save(update_fields=["locked_until"])
    issued = identity.start_password_reset(EMAIL)
    identity.complete_password_reset(EMAIL, issued.code, "a-brand-new-password")
    assert identity.authenticate(EMAIL, "a-brand-new-password")


# ─────────────────────────────────────────────────────────────────────────────
# Regression: the attempt counter must survive the raise
# ─────────────────────────────────────────────────────────────────────────────


def test_failed_attempts_persist_across_calls(user):
    """
    Regression. `redeem_code` was `@transaction.atomic` and raised on failure,
    so every increment of `attempts` was rolled back on the way out. The counter
    stayed at zero and the brute-force cap did nothing at all.

    A cap that silently does not apply is worse than no cap, because it is
    believed.
    """
    identity.issue_code(user, EmailCode.Purpose.VERIFY)

    with pytest.raises(identity.AuthError):
        identity.redeem_code(user, EmailCode.Purpose.VERIFY, "000000")

    assert EmailCode.objects.get(user=user).attempts == 1


def test_failed_reset_attempts_also_persist(user):
    """
    Same trap one level up: `complete_password_reset` wrapping the whole flow in
    an atomic block would roll back redeem_code's bookkeeping via the savepoint.
    """
    identity.start_password_reset(EMAIL)

    with pytest.raises(identity.AuthError):
        identity.complete_password_reset(EMAIL, "000000", "irrelevant-password")

    record = EmailCode.objects.get(user=user, purpose=EmailCode.Purpose.RESET)
    assert record.attempts == 1


def test_reset_code_brute_force_is_capped(user):
    """The reset path must be capped too — this one is an account takeover."""
    identity.start_password_reset(EMAIL)
    for _ in range(identity.MAX_CODE_ATTEMPTS):
        with pytest.raises(identity.AuthError):
            identity.complete_password_reset(EMAIL, "000000", "attacker-password")

    record = EmailCode.objects.get(user=user, purpose=EmailCode.Purpose.RESET)
    assert record.used_at is not None
    # And the original password still works.
    assert identity.authenticate(EMAIL, PASSWORD)
