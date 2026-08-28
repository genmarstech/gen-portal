"""
The identity boundary.

═══════════════════════════════════════════════════════════════════════════════
EVERY authentication operation in this application goes through this module.

Nothing else may call `User.objects.create_user`, `check_password`,
`django.contrib.auth.authenticate`, or touch `EmailCode` directly. Views and
serialisers call the functions below and nothing more.

WHY: AuthGate replaces this. When it lands, this module becomes an HTTP client
for AuthGate and the rest of the application does not change. That migration is
a day's work if this boundary holds, and a rewrite if auth logic has been
allowed to sprawl across thirty files. A `scripts/check_identity_boundary.py`
check enforces it in CI.
═══════════════════════════════════════════════════════════════════════════════

Failure handling here is deliberately uniform. Callers get an `AuthError` with a
`reason` for logging, and a `safe_message` that is the SAME regardless of
whether an address exists. Distinguishing "no such account" from "wrong
password" hands an attacker a free account-enumeration oracle.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from django.contrib.auth.hashers import check_password, make_password
from django.db import transaction
from django.utils import timezone

from .models import EmailCode, Organisation, Membership, User

# Lockout. Charter 05 §III promises a client a response, not a lockout that
# needs a support ticket to clear — so this expires on its own.
MAX_FAILED_SIGN_INS = 5
LOCKOUT_DURATION = timedelta(minutes=15)

# A wrong code should not be brute-forceable within the code's 15-minute life.
MAX_CODE_ATTEMPTS = 5

GENERIC_SIGN_IN_FAILURE = "That email address and password do not match."


class AuthError(Exception):
    """
    Authentication failed.

    `safe_message` is what the client sees and must never vary with whether the
    account exists. `reason` is for our logs only.
    """

    def __init__(self, reason: str, safe_message: str = GENERIC_SIGN_IN_FAILURE):
        super().__init__(reason)
        self.reason = reason
        self.safe_message = safe_message


class AccountLocked(AuthError):
    def __init__(self, until):
        super().__init__(
            "account_locked",
            "Too many failed attempts. Try again in a few minutes — "
            "the lock clears on its own.",
        )
        self.until = until


@dataclass(frozen=True)
class IssuedCode:
    """A freshly minted code. The plaintext exists only here and in the email."""

    user: User
    code: str
    expires_at: object


# ─────────────────────────────────────────────────────────────────────────────
# Sign in
# ─────────────────────────────────────────────────────────────────────────────


def authenticate(email: str, password: str) -> User:
    """
    Verify credentials. Raises AuthError on any failure.

    Runs a hash comparison even when the account does not exist, so the response
    time does not reveal which addresses are registered.
    """
    email = (email or "").strip().lower()
    user = User.objects.filter(email=email).first()

    if user is None:
        # Constant-ish time: burn a hash so a missing account is not faster.
        check_password(password or "", make_password("timing-equaliser"))
        raise AuthError("unknown_email")

    if user.is_locked:
        raise AccountLocked(user.locked_until)

    if not user.is_active:
        raise AuthError("inactive_account")

    if not user.check_password(password or ""):
        _register_failure(user)
        if user.is_locked:
            raise AccountLocked(user.locked_until)
        raise AuthError("bad_password")

    _clear_failures(user)
    return user


def _register_failure(user: User) -> None:
    user.failed_sign_ins += 1
    if user.failed_sign_ins >= MAX_FAILED_SIGN_INS:
        user.locked_until = timezone.now() + LOCKOUT_DURATION
        user.failed_sign_ins = 0
    user.save(update_fields=["failed_sign_ins", "locked_until"])


def _clear_failures(user: User) -> None:
    if user.failed_sign_ins or user.locked_until:
        user.failed_sign_ins = 0
        user.locked_until = None
        user.save(update_fields=["failed_sign_ins", "locked_until"])


# ─────────────────────────────────────────────────────────────────────────────
# Registration
# ─────────────────────────────────────────────────────────────────────────────


@transaction.atomic
def create_account(email: str, password: str, full_name: str = "") -> User:
    """
    Create an unverified account.

    Creates an ACCOUNT ONLY. It does not create an order, and it must not:
    Charter 02 §I gives qualification to the commercial partners and the
    capacity veto to the founder. Orders are created by staff after a signed
    SOW. Self-serve signup manufacturing work would route around both.
    """
    email = (email or "").strip().lower()
    if User.objects.filter(email=email).exists():
        # Caller must NOT surface this. See request_email_verification.
        raise AuthError("email_taken", "Check your inbox to continue.")
    return User.objects.create_user(
        email=email, password=password, full_name=(full_name or "").strip()
    )


# ─────────────────────────────────────────────────────────────────────────────
# Codes — email verification and password reset
# ─────────────────────────────────────────────────────────────────────────────


@transaction.atomic
def issue_code(user: User, purpose: str) -> IssuedCode:
    """Invalidate any outstanding codes for this purpose, then mint one."""
    EmailCode.objects.filter(user=user, purpose=purpose, used_at__isnull=True).update(
        used_at=timezone.now()
    )
    code = EmailCode.generate_code()
    record = EmailCode.objects.create(
        user=user,
        purpose=purpose,
        code_hash=make_password(code),
        expires_at=timezone.now() + EmailCode.LIFETIME,
    )
    return IssuedCode(user=user, code=code, expires_at=record.expires_at)


def redeem_code(user: User, purpose: str, code: str) -> None:
    """
    Consume a code. Raises AuthError if it is wrong, expired, or already used.

    ── WHY THE RAISE HAPPENS OUTSIDE THE TRANSACTION ────────────────────────────
    This function must NOT be decorated `@transaction.atomic`. It was, and the
    result was a silently non-functional brute-force cap: raising inside an
    atomic block rolls the block back, so every increment of `attempts` was
    discarded on its way out. The counter sat at zero forever and a six-digit
    code could be guessed without limit.

    So: do the work inside an explicit `atomic()` block, let it COMMIT, and
    raise afterwards. The lock still serialises concurrent redemptions, and the
    failure count actually persists.
    """
    invalid = AuthError("bad_code", "That code is not right, or it has expired.")
    failed = False

    with transaction.atomic():
        record = (
            EmailCode.objects.select_for_update()
            .filter(user=user, purpose=purpose, used_at__isnull=True)
            .order_by("-created_at")
            .first()
        )

        if record is None or not record.is_usable:
            # Nothing to persist; safe to raise directly.
            raise invalid

        if not check_password(code or "", record.code_hash):
            record.attempts += 1
            # Burn the code the moment the cap is REACHED, not on the next call,
            # so it never sits alive-but-capped.
            if record.attempts >= MAX_CODE_ATTEMPTS:
                record.used_at = timezone.now()
                record.save(update_fields=["attempts", "used_at"])
            else:
                record.save(update_fields=["attempts"])
            failed = True
        else:
            record.used_at = timezone.now()
            record.save(update_fields=["used_at"])

    if failed:
        raise invalid


def verify_email(user: User, code: str) -> None:
    redeem_code(user, EmailCode.Purpose.VERIFY, code)
    if not user.is_email_verified:
        user.email_verified_at = timezone.now()
        user.save(update_fields=["email_verified_at"])


def request_email_verification(email: str) -> IssuedCode | None:
    """
    Returns None when the address is unknown — the CALLER MUST respond
    identically either way. Whether an address is registered is not public.
    """
    user = User.objects.filter(email=(email or "").strip().lower()).first()
    if user is None:
        return None
    return issue_code(user, EmailCode.Purpose.VERIFY)


def start_password_reset(email: str) -> IssuedCode | None:
    """Same contract as request_email_verification: None is not an error."""
    user = User.objects.filter(email=(email or "").strip().lower()).first()
    if user is None:
        return None
    return issue_code(user, EmailCode.Purpose.RESET)


def complete_password_reset(email: str, code: str, new_password: str) -> User:
    """
    NOT decorated `@transaction.atomic`, and that is deliberate.

    Wrapping the whole function would re-introduce the bug redeem_code documents:
    a failed redemption raises, the outer block rolls back, and the attempt
    counter written by redeem_code is discarded along with it. Nested atomics are
    savepoints — an outer rollback undoes the inner work too.

    So the redemption commits on its own, and only the password write is wrapped.
    """
    user = User.objects.filter(email=(email or "").strip().lower()).first()
    if user is None:
        raise AuthError("unknown_email", "That code is not right, or it has expired.")

    # Commits its own attempt bookkeeping before raising.
    redeem_code(user, EmailCode.Purpose.RESET, code)

    with transaction.atomic():
        user.set_password(new_password)
        # A successful reset clears a lockout: the person proved inbox control.
        user.failed_sign_ins = 0
        user.locked_until = None
        user.save(update_fields=["password", "failed_sign_ins", "locked_until"])
    return user


def change_password(user: User, current_password: str, new_password: str) -> None:
    if not user.check_password(current_password or ""):
        raise AuthError("bad_password", "Your current password is not right.")
    user.set_password(new_password)
    user.save(update_fields=["password"])


# ─────────────────────────────────────────────────────────────────────────────
# Onboarding
# ─────────────────────────────────────────────────────────────────────────────


def has_organisation(user: User) -> bool:
    """Whether onboarding has already been completed for this account."""
    return Membership.objects.filter(user=user).exists()


@transaction.atomic
def attach_organisation(user: User, organisation_name: str) -> Organisation:
    """
    Onboarding: create the client's organisation and make them its owner.

    Still no order. This records who they are, not that work has been agreed.

    ── NOT IDEMPOTENT BY ACCIDENT ──────────────────────────────────────────────
    This unconditionally CREATES. Called twice — a double-clicked button, a
    retried request, a back-button resubmit — it would build a second
    organisation and a second membership, and the client would then see an
    account split across two orgs with their order visible under only one of
    them. The guard below is the whole reason this is not a two-line function.
    """
    name = (organisation_name or "").strip()
    if not name:
        raise AuthError("no_organisation_name", "Please give your organisation a name.")

    existing = Membership.objects.select_related("organisation").filter(user=user).first()
    if existing is not None:
        raise AuthError(
            "already_onboarded",
            "This account already belongs to an organisation.",
        )

    org = Organisation.objects.create(name=name)
    Membership.objects.create(
        user=user, organisation=org, role=Membership.Role.OWNER
    )
    return org
