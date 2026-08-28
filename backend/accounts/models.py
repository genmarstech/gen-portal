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
    is_active = models.BooleanField(default=True)

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
