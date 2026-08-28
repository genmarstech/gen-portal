"""
Onboarding API tests.

The load-bearing assertion in this file is that onboarding CANNOT create an
Order. Charter 02 §I puts qualification with the commercial partners and the
capacity veto with the founder; a self-serve form that produced an engagement
would route around both, and it would do so silently — the client would believe
work had started. Everything else here is ordinary form handling.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from accounts import identity
from accounts.models import Membership, Organisation
from portal.models import Enquiry, Order

pytestmark = pytest.mark.django_db

PASSWORD = "correct-horse-battery"

GOOD = {
    "full_name": "Wanjiru Kamau",
    "organisation_name": "Sarova Logistics",
    "problem": "Reconciling M-Pesa settlements against our invoice ledger by hand takes two days a week.",
    "monthly_cost": "Around KES 90,000 in staff time",
    "timeline": "Within three months",
    "budget_range": "KES 500,000 - 1,000,000",
}


@pytest.fixture
def verified(django_user_model):
    from django.utils import timezone

    user = identity.create_account("client@example.com", PASSWORD, "")
    user.email_verified_at = timezone.now()
    user.save()
    return user


def test_onboarding_creates_organisation_membership_and_enquiry(client, verified):
    client.force_login(verified)

    res = client.post(reverse("onboarding"), GOOD, content_type="application/json")

    assert res.status_code == 201
    assert res.json()["next"] == "/dashboard"

    org = Organisation.objects.get(name="Sarova Logistics")
    assert Membership.objects.filter(user=verified, organisation=org).exists()

    enquiry = Enquiry.objects.get(organisation=org)
    assert enquiry.submitted_by == verified
    assert enquiry.status == Enquiry.Status.NEW
    assert "M-Pesa" in enquiry.problem

    verified.refresh_from_db()
    assert verified.full_name == "Wanjiru Kamau"


def test_onboarding_does_not_create_an_order(client, verified):
    """
    The one that matters. An Enquiry is a request to be considered; an Order is
    a signed engagement. No client-facing endpoint may turn the first into the
    second.
    """
    client.force_login(verified)

    client.post(reverse("onboarding"), GOOD, content_type="application/json")

    assert Order.objects.count() == 0


def test_a_second_submission_does_not_split_the_account(client, verified):
    """
    A double-clicked button used to create a second organisation and a second
    membership. The account would then be spread across two orgs with any
    future order visible under only one of them.
    """
    client.force_login(verified)

    first = client.post(reverse("onboarding"), GOOD, content_type="application/json")
    second = client.post(
        reverse("onboarding"),
        {**GOOD, "organisation_name": "A Different Name"},
        content_type="application/json",
    )

    assert first.status_code == 201
    # Not an error: the account is already in the state the caller wanted.
    assert second.status_code == 200
    assert second.json()["next"] == "/dashboard"

    assert Organisation.objects.count() == 1
    assert Membership.objects.filter(user=verified).count() == 1
    assert Enquiry.objects.count() == 1


def test_onboarding_requires_authentication(client):
    res = client.post(reverse("onboarding"), GOOD, content_type="application/json")
    assert res.status_code in (401, 403)
    assert Organisation.objects.count() == 0


def test_onboarding_requires_a_verified_email(client):
    user = identity.create_account("unverified@example.com", PASSWORD, "")
    client.force_login(user)

    res = client.post(reverse("onboarding"), GOOD, content_type="application/json")

    assert res.status_code == 403
    assert Organisation.objects.count() == 0


def test_a_too_short_problem_is_rejected(client, verified):
    client.force_login(verified)

    res = client.post(
        reverse("onboarding"), {**GOOD, "problem": "help"}, content_type="application/json"
    )

    assert res.status_code == 400
    assert Organisation.objects.count() == 0


def test_the_optional_qualification_fields_may_be_blank(client, verified):
    """
    A prospect who does not know their budget still has a real problem. Forcing
    a number out of them teaches them to invent one.
    """
    client.force_login(verified)

    res = client.post(
        reverse("onboarding"),
        {
            "full_name": "Achieng Otieno",
            "organisation_name": "Otieno Freight",
            "problem": GOOD["problem"],
        },
        content_type="application/json",
    )

    assert res.status_code == 201
    assert Enquiry.objects.get().budget_range == ""


def test_a_blank_organisation_name_is_rejected(client, verified):
    client.force_login(verified)

    res = client.post(
        reverse("onboarding"),
        {**GOOD, "organisation_name": "   "},
        content_type="application/json",
    )

    assert res.status_code == 400
    assert Organisation.objects.count() == 0


def test_the_enquiry_is_not_readable_through_the_dashboard_api(client, verified):
    """
    Enquiries are internal working material for qualification. The client's own
    submission going back to them is harmless; what must not happen is it
    appearing in the orders API as though it were agreed work.
    """
    client.force_login(verified)
    client.post(reverse("onboarding"), GOOD, content_type="application/json")

    res = client.get(reverse("order-list"))

    assert res.json()["orders"] == []
    assert res.json()["has_orders"] is False


def test_the_order_list_reports_that_an_enquiry_exists(client, verified):
    """
    The empty state changes wording once someone has told us what they need.
    Asking "not talked to us yet?" of a client who just submitted a detailed
    enquiry reads as though nobody looked at it.
    """
    client.force_login(verified)
    before = client.get(reverse("order-list")).json()
    assert before["has_enquiry"] is False

    client.post(reverse("onboarding"), GOOD, content_type="application/json")

    after = client.get(reverse("order-list")).json()
    assert after["has_enquiry"] is True
    # Still no order. An enquiry is not an engagement.
    assert after["has_orders"] is False
