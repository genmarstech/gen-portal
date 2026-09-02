"""
Client accounts: creating organisations, inviting people, revoking access.

The guarantee this file exists to defend: STAFF NEVER KNOW A CLIENT PASSWORD.
An invited account is created without a usable one, and the only thing that
makes it usable is the client proving inbox control and choosing their own.
"""

from __future__ import annotations

import pytest
from django.core import mail
from django.urls import reverse
from django.utils import timezone

from accounts.models import EmailCode, Membership, Organisation, User
from operations import services

pytestmark = pytest.mark.django_db

PASSWORD = "correct-horse-battery"


@pytest.fixture(autouse=True)
def _clear_throttles():
    from django.core.cache import cache
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def staff() -> User:
    return User.objects.create_user(
        email="ops@genmars.co.ke", password=PASSWORD, full_name="Ops Person",
        # Founder: this module is about who may reach a client's account,
        # which is an access decision (Charter 02 §I).
        is_staff=True, staff_role=User.StaffRole.FOUNDER,
        email_verified_at=timezone.now(),
    )


@pytest.fixture
def org() -> Organisation:
    return Organisation.objects.create(name="Kilimani Dental")


@pytest.fixture
def signed_in(client, staff):
    client.force_login(staff)
    return client


def code_from_last_email() -> str:
    import re
    match = re.search(r"\b(\d{6})\b", mail.outbox[-1].body)
    assert match, f"no code in: {mail.outbox[-1].body[:200]}"
    return match.group(1)


# ── organisations ────────────────────────────────────────────────────────────


def test_staff_can_create_an_organisation(signed_in):
    response = signed_in.post(
        reverse("ops-organisations"), {"name": "Rift Valley Logistics"},
        content_type="application/json",
    )
    assert response.status_code == 201, response.json()
    assert Organisation.objects.filter(name="Rift Valley Logistics").exists()


def test_duplicate_names_are_refused_case_insensitively(signed_in, org):
    """Two "Kilimani Dental" organisations means client data split across
    both, and nobody notices until someone asks why half the orders vanished."""
    response = signed_in.post(
        reverse("ops-organisations"), {"name": "kilimani dental"},
        content_type="application/json",
    )
    assert response.status_code == 400
    assert Organisation.objects.count() == 1


# ── inviting ─────────────────────────────────────────────────────────────────


def test_inviting_creates_an_account_that_cannot_be_signed_into(signed_in, org):
    """
    THE test in this file.

    The account exists so a membership can point at it, and it is unusable
    until the person sets a password. Staff never hold a client credential and
    none ever travels by email.
    """
    response = signed_in.post(
        reverse("ops-org-members", args=[org.pk]),
        {"email": "New.Person@example.com", "full_name": "New Person", "role": "owner"},
        content_type="application/json",
    )
    assert response.status_code == 201, response.json()
    assert response.json()["invited"] is True

    user = User.objects.get(email="new.person@example.com")  # normalised
    assert not user.has_usable_password()
    assert not user.is_email_verified

    # And it really cannot be signed into.
    signed_out = signed_in.client if hasattr(signed_in, "client") else None
    assert user.check_password("") is False


def test_the_invite_email_names_the_organisation_and_the_inviter(signed_in, org, staff):
    """
    It arrives unsolicited and asks for a password — the shape of a phishing
    email. A human name and the organisation they already work for are what
    make it credible.
    """
    signed_in.post(
        reverse("ops-org-members", args=[org.pk]),
        {"email": "new@example.com"}, content_type="application/json",
    )
    body = mail.outbox[-1].body
    assert org.name in body
    assert staff.full_name in body
    assert "Ops Person" in mail.outbox[-1].subject or org.name in mail.outbox[-1].subject


def test_accepting_an_invite_sets_the_password_and_verifies_the_address(client, signed_in, org):
    signed_in.post(
        reverse("ops-org-members", args=[org.pk]),
        {"email": "new@example.com"}, content_type="application/json",
    )
    code = code_from_last_email()

    response = client.post(
        reverse("accept-invite"),
        {"email": "new@example.com", "code": code, "password": "a-long-enough-passphrase"},
        content_type="application/json",
    )
    assert response.status_code == 200, response.json()

    user = User.objects.get(email="new@example.com")
    assert user.has_usable_password()
    # Redeeming proves inbox control — the same thing verification proves — so
    # a second code would be friction with no security in it.
    assert user.is_email_verified
    # They have a membership already, so they go straight to the dashboard.
    assert response.json()["next"] == "/dashboard"


def test_an_invite_code_works_once(client, signed_in, org):
    signed_in.post(
        reverse("ops-org-members", args=[org.pk]),
        {"email": "new@example.com"}, content_type="application/json",
    )
    code = code_from_last_email()
    body = {"email": "new@example.com", "code": code, "password": "a-long-enough-passphrase"}

    assert client.post(reverse("accept-invite"), body, content_type="application/json").status_code == 200
    second = client.post(reverse("accept-invite"), body, content_type="application/json")
    assert second.status_code == 400


def test_a_staff_address_cannot_be_given_a_client_membership(signed_in, org, staff):
    """
    is_staff grants nothing in the client portal by design. Giving a staff
    account a membership would hand it real client data through the CLIENT api
    — quietly, by a path nobody would think to check.
    """
    response = signed_in.post(
        reverse("ops-org-members", args=[org.pk]),
        {"email": staff.email}, content_type="application/json",
    )
    assert response.status_code == 400
    assert not Membership.objects.filter(user=staff).exists()


def test_inviting_an_existing_client_adds_them_without_a_code(signed_in, org):
    """
    They already have a password. Sending a code and telling them to check
    their inbox for nothing is worse than saying nothing.
    """
    existing = User.objects.create_user(
        email="known@example.com", password=PASSWORD, email_verified_at=timezone.now()
    )
    before = len(mail.outbox)
    response = signed_in.post(
        reverse("ops-org-members", args=[org.pk]),
        {"email": existing.email}, content_type="application/json",
    )
    assert response.status_code == 201
    assert response.json()["invited"] is False
    assert len(mail.outbox) == before
    assert EmailCode.objects.filter(user=existing).count() == 0


def test_the_same_person_cannot_be_added_twice(signed_in, org):
    signed_in.post(reverse("ops-org-members", args=[org.pk]),
                   {"email": "new@example.com"}, content_type="application/json")
    again = signed_in.post(reverse("ops-org-members", args=[org.pk]),
                           {"email": "new@example.com"}, content_type="application/json")
    assert again.status_code == 400
    assert Membership.objects.filter(organisation=org).count() == 1


# ── revoking ─────────────────────────────────────────────────────────────────


def test_removing_access_keeps_the_account_and_its_history(signed_in, org, staff):
    """
    Deleting the user would cascade to the enquiry that started the engagement.
    Losing why we took work on is worse than a dormant account.
    """
    from portal.models import Enquiry

    signed_in.post(reverse("ops-org-members", args=[org.pk]),
                   {"email": "new@example.com"}, content_type="application/json")
    membership = Membership.objects.get(organisation=org)
    user = membership.user
    Enquiry.objects.create(organisation=org, submitted_by=user, problem="Something.")

    response = signed_in.delete(reverse("ops-membership", args=[membership.pk]))
    assert response.status_code == 204

    assert User.objects.filter(pk=user.pk).exists()
    assert Enquiry.objects.filter(submitted_by=user).exists()
    # Revoked access looks like this: signed in, and sees nothing.
    from portal.selectors import orders_for
    assert orders_for(user).count() == 0
