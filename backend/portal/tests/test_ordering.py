"""
Ordering a specific service from the website.

genmars.co.ke lists services and tiers; clicking one sends the visitor to the
portal to sign in and describe their problem. What they clicked has to survive
that trip, or the commercial partners open an enquiry with no idea which of the
seven offerings it is about.
"""

from __future__ import annotations

import pytest
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from portal.models import Enquiry, Service

pytestmark = pytest.mark.django_db

PASSWORD = "correct-horse-battery"


@pytest.fixture(autouse=True)
def _clear_throttles():
    from django.core.cache import cache
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def visitor() -> User:
    return User.objects.create_user(
        email="new@example.com", password=PASSWORD, email_verified_at=timezone.now()
    )


@pytest.fixture
def service() -> Service:
    return Service.objects.create(
        name="Implementation & configuration",
        # The slug genmars.co.ke uses. The join between the two systems.
        slug="implementation",
        summary="One-time setup, configuration, migration and go-live.",
    )


def onboard(client, **extra):
    body = {
        "full_name": "A Person",
        "organisation_name": "Kilimani Dental",
        "problem": "We reconcile M-Pesa against invoices by hand, two days a week.",
    }
    body.update(extra)
    return client.post(reverse("onboarding"), body, content_type="application/json")


def test_the_service_and_tier_survive_the_trip_to_the_portal(client, visitor, service):
    client.force_login(visitor)
    assert onboard(client, service="implementation", tier="Business Setup").status_code == 201

    enquiry = Enquiry.objects.get()
    assert enquiry.service == service
    assert enquiry.tier == "Business Setup"


def test_an_enquiry_without_a_service_is_still_accepted(client, visitor):
    """
    The open route. Someone who does not know which service they need has a
    real problem, and a form that forced them to pick one would be asking them
    to guess at our catalogue before we have spoken.
    """
    client.force_login(visitor)
    assert onboard(client).status_code == 201

    enquiry = Enquiry.objects.get()
    assert enquiry.service is None
    assert enquiry.tier == ""


def test_an_unknown_slug_is_dropped_rather_than_refused(client, visitor):
    """
    A renamed or retired service must never cost us the enquiry. The visitor
    did nothing wrong, and losing the attribution is far cheaper than losing
    the lead.
    """
    client.force_login(visitor)
    response = onboard(client, service="a-service-we-retired", tier="Gold")
    assert response.status_code == 201

    enquiry = Enquiry.objects.get()
    assert enquiry.service is None
    # The tier is kept anyway — "they asked for Gold" is useful on its own.
    assert enquiry.tier == "Gold"


def test_a_retired_service_does_not_attach(client, visitor, service):
    service.is_active = False
    service.save(update_fields=["is_active"])

    client.force_login(visitor)
    assert onboard(client, service="implementation", tier="Essential").status_code == 201
    assert Enquiry.objects.get().service is None


def test_retiring_a_service_later_keeps_the_enquiries(client, visitor, service):
    """
    SET_NULL, not CASCADE. The record of who asked for a service is exactly
    what tells you whether retiring it was right.
    """
    client.force_login(visitor)
    onboard(client, service="implementation", tier="Essential Setup")
    enquiry = Enquiry.objects.get()

    service.delete()

    enquiry.refresh_from_db()
    assert enquiry.service is None
    assert enquiry.tier == "Essential Setup"
    assert Enquiry.objects.count() == 1


def test_operations_can_see_what_was_ordered(client, visitor, service):
    client.force_login(visitor)
    onboard(client, service="implementation", tier="Enterprise Setup")

    staff = User.objects.create_user(
        email="ops@genmars.co.ke", password=PASSWORD, is_staff=True,
        staff_role=User.StaffRole.COMMERCIAL, email_verified_at=timezone.now(),
    )
    client.force_login(staff)
    row = client.get(reverse("ops-enquiries")).json()["enquiries"][0]

    assert row["service_name"] == "Implementation & configuration"
    assert row["tier"] == "Enterprise Setup"


def test_an_overlong_tier_label_cannot_overflow_the_column(client, visitor, service):
    """The value arrives from a query string, so its length is the visitor's
    choice, not ours."""
    client.force_login(visitor)
    response = onboard(client, service="implementation", tier="x" * 500)
    # Refused by the serializer's max_length rather than truncated silently, or
    # accepted and trimmed — either is fine, a 500 is not.
    assert response.status_code in {201, 400}
    if response.status_code == 201:
        assert len(Enquiry.objects.get().tier) <= 120


# ── a client who already has an account ──────────────────────────────────────


@pytest.fixture
def returning(visitor) -> User:
    """A client who signed up some time ago and has an organisation."""
    from accounts.models import Membership, Organisation

    org = Organisation.objects.create(name="Kilimani Dental")
    Membership.objects.create(user=visitor, organisation=org)
    return visitor


def test_an_existing_client_can_order_a_second_service(client, returning, service):
    """
    ═══════════════════════════════════════════════════════════════════════════
    THE BUG THIS ENDPOINT EXISTS FOR.

    Every tier on genmars.co.ke used to link to /sign-up. Onboarding runs once
    and refuses a second time with `already_onboarded`, which the API answers
    by redirecting to /dashboard — so a returning client who clicked "Order
    Business Setup" landed on their dashboard with the order silently thrown
    away. No error, no enquiry, and nobody here ever learned they had asked.
    ═══════════════════════════════════════════════════════════════════════════
    """
    client.force_login(returning)
    response = client.post(
        reverse("enquiry-create"),
        {
            "problem": "We need the second location set up the same way.",
            "service": "implementation",
            "tier": "Business Setup",
            "timeline": "Within three months",
        },
        content_type="application/json",
    )
    assert response.status_code == 201, response.json()

    enquiry = Enquiry.objects.get()
    assert enquiry.service == service
    assert enquiry.tier == "Business Setup"
    assert enquiry.organisation.name == "Kilimani Dental"
    assert enquiry.submitted_by == returning


def test_the_old_route_still_refuses_a_second_onboarding(client, returning):
    """
    The behaviour that caused the bug is unchanged and correct — onboarding
    creates an organisation, and doing that twice would give one client two.
    What changed is that ordering no longer goes through it.
    """
    response = client.post(
        reverse("onboarding"),
        {
            "full_name": "A Person",
            "organisation_name": "A Different Name Entirely",
            "problem": "We reconcile by hand and it takes two days a week.",
        },
        content_type="application/json",
    )
    client.force_login(returning)
    response = client.post(
        reverse("onboarding"),
        {
            "full_name": "A Person",
            "organisation_name": "A Different Name Entirely",
            "problem": "We reconcile by hand and it takes two days a week.",
        },
        content_type="application/json",
    )
    assert response.json()["next"] == "/dashboard"
    # And it did NOT rename their organisation.
    returning.refresh_from_db()
    assert returning.memberships.first().organisation.name == "Kilimani Dental"


def test_ordering_cannot_rename_the_organisation(client, returning, service):
    """The enquiry endpoint takes no organisation name, so a second submission
    has nothing to rename with."""
    client.force_login(returning)
    client.post(
        reverse("enquiry-create"),
        {
            "problem": "Something else we need doing here, please.",
            "organisation_name": "Hijacked Ltd",
            "service": "implementation",
        },
        content_type="application/json",
    )
    assert returning.memberships.first().organisation.name == "Kilimani Dental"


def test_a_signed_in_client_with_no_organisation_is_routed_not_errored(client, visitor):
    """Signed in, verified, never finished signing up. An ordinary state, so it
    routes to onboarding rather than showing an error nobody can act on."""
    client.force_login(visitor)
    response = client.post(
        reverse("enquiry-create"),
        {"problem": "We would like to talk about the payments work."},
        content_type="application/json",
    )
    assert response.status_code == 409
    assert response.json()["next"] == "/onboarding"


def test_an_unverified_client_cannot_file_an_enquiry(client, returning):
    returning.email_verified_at = None
    returning.save(update_fields=["email_verified_at"])
    client.force_login(returning)
    response = client.post(
        reverse("enquiry-create"),
        {"problem": "We would like the reconciliation work doing."},
        content_type="application/json",
    )
    assert response.status_code == 403


def test_an_anonymous_visitor_cannot_file_an_enquiry(client):
    response = client.post(
        reverse("enquiry-create"),
        {"problem": "We would like the reconciliation work doing."},
        content_type="application/json",
    )
    assert response.status_code in {401, 403}
    assert Enquiry.objects.count() == 0


def test_a_one_word_problem_is_refused(client, returning):
    """This text is what the commercial partners qualify against."""
    client.force_login(returning)
    response = client.post(
        reverse("enquiry-create"), {"problem": "hi"}, content_type="application/json"
    )
    assert response.status_code == 400
    assert Enquiry.objects.count() == 0


def test_filing_enquiries_is_throttled(client, returning):
    client.force_login(returning)
    codes = [
        client.post(
            reverse("enquiry-create"),
            {"problem": f"We need help with the thing number {i} please."},
            content_type="application/json",
        ).status_code
        for i in range(16)
    ]
    assert 429 in codes
