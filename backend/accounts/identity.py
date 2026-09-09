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

import secrets
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


@transaction.atomic
def create_invited_account(
    email: str,
    full_name: str = "",
    *,
    is_staff: bool = False,
    staff_role: str = "",
) -> User:
    """
    Create an account somebody else is bringing into being, with NO password.

    ── WHY THIS EXISTS SEPARATELY FROM create_account ──────────────────────────

    An invite inverts who chooses the password. `create_account` is a person
    signing themselves up and choosing one; this is staff adding a colleague or
    a client contact, and the invitee sets theirs by redeeming the code. There
    is no moment at which anyone but the account holder knows the password —
    which is the property that makes an invite safe to send by email.

    Until then the account holds an UNUSABLE password: not an empty one, not a
    guessable placeholder, a hash no input can ever match. `create_user` with
    `password=None` already produces one; `set_unusable_password` after it is
    belt and braces against that behaviour changing under us, because the whole
    guarantee rests on it. Both call sites used to carry that pair themselves —
    which is exactly the sprawl this module exists to prevent, since a third
    call site that copied only the first half would create an account nobody
    can sign into and nobody can tell is broken.

    Callers are expected to have already refused duplicates with a message
    suited to their own screen; the check here is the backstop, and it is
    deliberately the same "email_taken" AuthError create_account raises.
    """
    email = (email or "").strip().lower()
    if User.objects.filter(email=email).exists():
        raise AuthError("email_taken", "That address already has an account.")

    user = User.objects.create_user(
        email=email,
        password=None,
        full_name=(full_name or "").strip(),
        is_staff=is_staff,
        staff_role=staff_role,
    )
    user.set_unusable_password()
    user.save(update_fields=["password"])
    return user


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


def accept_invite(email: str, code: str, new_password: str) -> User:
    """
    Set the password on an account Genmars created, and verify the address.

    ── WHY STAFF NEVER SET A CLIENT'S PASSWORD ─────────────────────────────────
    The obvious implementation of "add a client" is to create the account with a
    password and tell them what it is. That is wrong in three ways at once: a
    Genmars employee would know a client's credential, the credential would
    travel by email in plain text, and the client could not prove afterwards
    that only they could have signed in.

    So an invited account is created with an UNUSABLE password. The only way it
    becomes usable is the person proving inbox control and choosing their own,
    which is this function. Until then the account exists and cannot be signed
    into by anybody, including us.

    Redeeming the code also VERIFIES the address, because succeeding here proves
    exactly what the verification step proves — that the person reading the
    inbox is the person acting. Sending a second code to confirm what the first
    one just established is friction with no security in it.

    Not decorated @transaction.atomic, for the reason complete_password_reset
    documents: redeem_code commits its own attempt bookkeeping before raising,
    and an outer rollback would discard it.
    """
    user = User.objects.filter(email=(email or "").strip().lower()).first()
    if user is None:
        raise AuthError(
            "unknown_email", "That code is not right, or it has expired."
        )

    redeem_code(user, EmailCode.Purpose.INVITE, code)

    with transaction.atomic():
        user.set_password(new_password)
        if user.email_verified_at is None:
            user.email_verified_at = timezone.now()
        user.failed_sign_ins = 0
        user.locked_until = None
        user.save(
            update_fields=[
                "password",
                "email_verified_at",
                "failed_sign_ins",
                "locked_until",
            ]
        )
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


# ─────────────────────────────────────────────────────────────────────────────
# Signing in to a sibling application
# ─────────────────────────────────────────────────────────────────────────────
#
# ═══════════════════════════════════════════════════════════════════════════════
# THIS IS HERE BECAUSE IT IS AN IDENTITY OPERATION, NOT A PORTAL FEATURE.
#
# Redeeming a grant tells another application who somebody is. That is the same
# class of act as signing them in, and it belongs behind the same boundary as
# `authenticate` — one module to audit, one module to move the day identity
# leaves this codebase.
#
# The models live in portal/models.py, beside the system registry they hang off.
# They are imported inside the functions: portal imports accounts, so importing
# portal at the top of this file would close the loop.
# ═══════════════════════════════════════════════════════════════════════════════
#
# The shape, once:
#
#   1. A sibling sends a person here with its client_id and a redirect address.
#   2. They sign in normally, or already are. NO ACCOUNT MEANS NO ENTRY — they
#      are sent to sign up here, not given an account over there.
#   3. They see which application is asking and agree.
#   4. We hand the browser back with a code that lives ninety seconds.
#   5. The sibling's SERVER swaps that code, plus its own secret, for one answer
#      to one question: who was that. It gets nothing else and nothing lasting.

SIGN_ON_GRANT_LIFETIME = timedelta(seconds=90)

# Recognisable on sight. A credential found in a paste, a log or a repository
# should announce what it is and where to revoke it — the hours between a leak
# and someone realising what they are looking at are the expensive ones.
CLIENT_ID_PREFIX = "gsso_"
CLIENT_SECRET_PREFIX = "gsec_"
GRANT_CODE_PREFIX = "gsc_"

# Enough entropy that guessing is not a strategy, and a clear prefix kept only
# as a lookup index — the same trade SystemKey documents at length.
SECRET_BYTES = 32
PREFIX_LENGTH = 12

# One message for every way redemption can fail. A sibling learning that its
# secret was right but its code was stale is learning which half to attack, and
# the legitimate caller does not need the distinction — it retries the whole
# flow either way.
GRANT_REFUSED = "That sign-in could not be completed. Start again."


@dataclass(frozen=True)
class IssuedSecret:
    """A freshly minted client secret. The plaintext exists only here."""

    secret: str
    prefix: str


def new_client_id() -> str:
    """Public, not secret. Travels in a URL and appears in logs."""
    return CLIENT_ID_PREFIX + secrets.token_urlsafe(12)


def issue_client_secret(app) -> IssuedSecret:
    """
    Replace the app's secret and return the new one ONCE.

    Rotation is destructive on purpose: there is no second live secret and no
    overlap window. A sibling is one deploy, and two valid secrets is a state
    somebody forgets to leave — the old one then keeps working for a year.
    """
    secret = CLIENT_SECRET_PREFIX + secrets.token_urlsafe(SECRET_BYTES)
    app.secret_prefix = secret[:PREFIX_LENGTH]
    app.secret_hashed = make_password(secret)
    app.secret_set_at = timezone.now()
    app.save(update_fields=["secret_prefix", "secret_hashed", "secret_set_at", "updated_at"])
    return IssuedSecret(secret=secret, prefix=app.secret_prefix)


def find_sign_on_app(client_id: str):
    """
    Resolve a client_id to an app, or None. Does not check whether it is usable.

    Used by the consent screen, which must be able to say "that link is not
    valid" without a session — so it deliberately reveals only that a client_id
    is or is not known, which is public information by construction.
    """
    from portal.models import SignOnApp

    client_id = (client_id or "").strip()
    if not client_id.startswith(CLIENT_ID_PREFIX):
        return None
    return (
        SignOnApp.objects.select_related("system")
        .filter(client_id=client_id)
        .first()
    )


@transaction.atomic
def issue_grant(*, app, user: User, redirect_uri: str) -> str:
    """
    Mint the one-time code. Returns it; it is never recoverable afterwards.

    The caller has already established that this app is usable, that this
    redirect address is registered, and that this user is admitted. This
    function does not re-decide those — it records the decision.
    """
    from portal.models import SignOnGrant

    code = GRANT_CODE_PREFIX + secrets.token_urlsafe(SECRET_BYTES)
    SignOnGrant.objects.create(
        app=app,
        user=user,
        code_prefix=code[:PREFIX_LENGTH],
        code_hashed=make_password(code),
        redirect_uri=redirect_uri,
        expires_at=timezone.now() + SIGN_ON_GRANT_LIFETIME,
    )
    return code


def redeem_grant(*, client_id: str, client_secret: str, code: str, redirect_uri: str) -> User:
    """
    Swap a code for the person it stands for. Raises AuthError on any failure.

    Called by a sibling's SERVER, never by a browser.

    ── WHY THE ORDER OF CHECKS IS THIS ORDER ───────────────────────────────────

    The secret is verified BEFORE the code is looked up. A caller who cannot
    prove it is the application never reaches the code table at all, so the
    endpoint cannot be used to probe which codes exist.

    ── WHY A CODE IS BURNED WHEN IT MATCHES BUT SOMETHING ELSE DOES NOT ─────────

    A code that has been presented is spent, even if the presentation was
    wrong. Otherwise a code intercepted in a log could be tried against every
    registered redirect address until one worked.

    ── THE ROLLBACK TRAP ───────────────────────────────────────────────────────

    Same one `redeem_code` documents above: the burn must COMMIT before the
    refusal is raised. Raising inside the atomic block would roll back the very
    write that makes the code single-use, and single-use is most of the point.
    """
    from portal.models import SignOnGrant

    refused = AuthError("sign_on_refused", GRANT_REFUSED)

    app = find_sign_on_app(client_id)
    if app is None or not app.is_usable:
        raise refused

    if not app.secret_hashed or not check_password(client_secret or "", app.secret_hashed):
        raise refused

    presented = (code or "").strip()
    if not presented.startswith(GRANT_CODE_PREFIX):
        raise refused

    burned = False
    grant = None

    with transaction.atomic():
        candidates = (
            SignOnGrant.objects.select_for_update()
            .select_related("user")
            .filter(app=app, code_prefix=presented[:PREFIX_LENGTH], used_at__isnull=True)
        )
        match = next(
            (g for g in candidates if check_password(presented, g.code_hashed)), None
        )

        if match is not None:
            now = timezone.now()
            match.used_at = now
            match.save(update_fields=["used_at"])

            usable = now < match.expires_at
            same_place = match.redirect_uri == (redirect_uri or "").strip()
            still_allowed = app.admits(match.user)
            if usable and same_place and still_allowed:
                grant = match
            else:
                burned = True

    if grant is None:
        # Outside the block, so the burn above is already committed.
        raise AuthError("sign_on_refused_burned" if burned else "sign_on_refused", GRANT_REFUSED)

    return grant.user
