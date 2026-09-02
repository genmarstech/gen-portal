"""
Accounts — identity and organisation membership.

EMAIL IS THE USERNAME. Set at the first migration on purpose: swapping
USERNAME_FIELD later means a data migration across every foreign key that
touches auth, and it is one of the few Django decisions that is genuinely
painful to reverse.

Orders belong to an ORGANISATION, not a person (see portal.models). A client's
staff turn over; their project should not become unreachable because the one
person who signed up has left.
"""

from __future__ import annotations

import secrets
from datetime import timedelta

from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.conf import settings
from django.db import models
from django.utils import timezone


class UserManager(BaseUserManager):
    """Manager keyed on email rather than a username."""

    use_in_migrations = True

    def _create(self, email: str, password: str | None, **extra):
        if not email:
            raise ValueError("An email address is required.")
        email = self.normalize_email(email).lower()
        user = self.model(email=email, **extra)
        # set_password runs the configured hasher (Argon2 — see settings).
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email: str, password: str | None = None, **extra):
        extra.setdefault("is_staff", False)
        extra.setdefault("is_superuser", False)
        return self._create(email, password, **extra)

    def create_superuser(self, email: str, password: str, **extra):
        extra.setdefault("is_staff", True)
        # A superuser is a founder. `createsuperuser` is how the first account
        # on a fresh deployment is made, and one that could not grant roles
        # would leave the system with no way to bootstrap authority.
        extra.setdefault("staff_role", "founder")
        extra.setdefault("is_superuser", True)
        extra.setdefault("is_active", True)
        extra.setdefault("email_verified_at", timezone.now())
        if not extra["is_staff"] or not extra["is_superuser"]:
            raise ValueError("A superuser must have is_staff and is_superuser set.")
        return self._create(email, password, **extra)


class User(AbstractBaseUser, PermissionsMixin):
    email = models.EmailField(unique=True, db_index=True)
    full_name = models.CharField(max_length=200, blank=True)

    # Charter 03 §IV Tier 1 — least privilege. `is_staff` means Genmars, not
    # "admin": every client-facing queryset filters on membership, never on this.
    is_staff = models.BooleanField(default=False)
    is_active = models.BooleanField(
        default=True,
        help_text=(
            "False revokes access without deleting the account. Django refuses "
            "to authenticate an inactive user, so this is a real revocation — "
            "and the person's authorship of notes, gates and decisions survives, "
            "which deleting them would destroy."
        ),
    )

    class StaffRole(models.TextChoices):
        """
        Who decides what. Taken from Charter 02 §I rather than invented.

        FOUNDER holds the capacity veto and the sole call on pricing and public
        statements. COMMERCIAL holds qualification — whether an enquiry becomes
        work. DELIVERY builds it.

        THREE ROLES, NOT A PERMISSION MATRIX. This company is three people
        (Charter 01 §VII, Stage 0). A matrix of checkboxes is machinery nobody
        here needs, and Charter 03 §I says a thing enters the stack only when
        what is already there cannot do the job. Named roles that mirror the
        actual division of authority can do the job.
        """

        FOUNDER = "founder", "Founder"
        COMMERCIAL = "commercial", "Commercial"
        DELIVERY = "delivery", "Delivery"

    staff_role = models.CharField(
        max_length=16,
        choices=StaffRole.choices,
        blank=True,
        default="",
        help_text=(
            "Empty for client accounts. A staff account with no role can READ "
            "operations and change nothing — the safe state for someone whose "
            "role has not been decided yet."
        ),
    )

    email_verified_at = models.DateTimeField(null=True, blank=True)
    date_joined = models.DateTimeField(default=timezone.now)

    # Lockout state. Kept on the user rather than in cache so a restart does not
    # clear an in-progress attack.
    failed_sign_ins = models.PositiveIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS: list[str] = []

    class Meta:
        ordering = ["email"]

    def __str__(self) -> str:
        return self.email

    @property
    def is_email_verified(self) -> bool:
        return self.email_verified_at is not None

    # ── what this account may do ────────────────────────────────────────────
    #
    # READ IS SHARED, WRITE IS SCOPED. In a three-person company, hiding the
    # work from each other would be theatre; deciding who may commit the
    # company is not. So every staff account reads everything, and these gate
    # the three things Charter 02 §I actually reserves.

    @property
    def can_qualify(self) -> bool:
        """Turn an enquiry into work. Charter 02 §I — qualification is the
        commercial partners', and capacity is the founder's veto."""
        return self.is_staff and self.staff_role in {
            self.StaffRole.FOUNDER,
            self.StaffRole.COMMERCIAL,
        }

    @property
    def can_commit(self) -> bool:
        """Issue or sign a statement of work, and set what services cost us to
        promise. Money and commitment, which is the same authority."""
        return self.is_staff and self.staff_role in {
            self.StaffRole.FOUNDER,
            self.StaffRole.COMMERCIAL,
        }

    @property
    def can_manage_access(self) -> bool:
        """Change roles, invite staff, deactivate accounts.

        FOUNDER ONLY, and deliberately the narrowest of the three: this is the
        permission that can grant every other permission, including to itself.
        """
        return self.is_staff and self.staff_role == self.StaffRole.FOUNDER

    @property
    def is_locked(self) -> bool:
        return self.locked_until is not None and self.locked_until > timezone.now()


class Organisation(models.Model):
    """The client company. Orders hang off this, not off a user."""

    name = models.CharField(max_length=200)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class Membership(models.Model):
    """
    User ↔ Organisation.

    Every client-facing queryset in portal/ filters through this. It is the
    single mechanism that stops Organisation A reading Organisation B's order,
    and there is a test that proves it.
    """

    class Role(models.TextChoices):
        OWNER = "owner", "Owner"
        MEMBER = "member", "Member"

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="memberships")
    organisation = models.ForeignKey(
        Organisation, on_delete=models.CASCADE, related_name="memberships"
    )
    role = models.CharField(max_length=16, choices=Role.choices, default=Role.MEMBER)

    receives_updates = models.BooleanField(
        default=True,
        help_text=(
            "Whether this person is emailed when a progress note is published. "
            "Service mail about their own engagement, not marketing — but a "
            "second or third person at a client often does not want every note, "
            "and having no way to stop it is how service mail becomes marketing "
            "in the recipient's mind."
        ),
    )

    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="memberships_created",
        limit_choices_to={"is_staff": True},
        help_text=(
            "Null for the person who created the organisation through "
            "onboarding. Set when Genmars added them — Charter 01 §V, nothing "
            "ships without a named owner, and access is a thing that ships."
        ),
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("user", "organisation")]

    def __str__(self) -> str:
        return f"{self.user.email} @ {self.organisation.name} ({self.role})"


class EmailCode(models.Model):
    """
    Single-use, short-lived code for email verification and password reset.

    Stored HASHED. A leaked database dump should not hand over live codes, and
    these are credentials for the duration of their life.
    """

    class Purpose(models.TextChoices):
        VERIFY = "verify", "Verify email"
        RESET = "reset", "Password reset"
        # An invitation is a password-set for an account the person did not
        # create. Kept distinct from RESET so the email can say what actually
        # happened — "Genmars has added you to Kilimani Dental" rather than
        # "reset your password", which to someone who never had one reads as a
        # phishing attempt.
        INVITE = "invite", "Invitation"

    LIFETIME = timedelta(minutes=15)
    LENGTH = 6

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="codes")
    purpose = models.CharField(max_length=16, choices=Purpose.choices)
    code_hash = models.CharField(max_length=128)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    attempts = models.PositiveIntegerField(default=0)

    class Meta:
        indexes = [models.Index(fields=["user", "purpose", "used_at"])]

    @property
    def is_usable(self) -> bool:
        return self.used_at is None and self.expires_at > timezone.now()

    @staticmethod
    def generate_code() -> str:
        """Cryptographically random digits — never random.randint."""
        return "".join(secrets.choice("0123456789") for _ in range(EmailCode.LENGTH))
