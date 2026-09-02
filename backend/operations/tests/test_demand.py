"""
What is selling.

The point of this screen is deciding what to invest in, so the failure mode is
not a crash — it is a number that quietly means something other than what it
says. These tests pin the meanings.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from accounts.models import Membership, Organisation, User
from operations import selectors, services
from portal.models import Enquiry, Invoice, Service

pytestmark = pytest.mark.django_db

PASSWORD = "correct-horse-battery"


@pytest.fixture
def staff() -> User:
    return User.objects.create_user(
        email="ops@genmars.co.ke", password=PASSWORD, full_name="Ops",
        is_staff=True, staff_role=User.StaffRole.FOUNDER,
        email_verified_at=timezone.now(),
    )


@pytest.fixture
def catalogue():
    return {
        "implementation": Service.objects.create(
            name="Implementation", slug="implementation", summary="Setup."
        ),
        "training": Service.objects.create(
            name="Training", slug="training", summary="Courses."
        ),
    }


def _enquiry(service, tier="", *, org_name="Client Co"):
    org = Organisation.objects.create(name=org_name)
    user = User.objects.create_user(
        email=f"{org_name.replace(' ', '').lower()}@example.com", password=PASSWORD
    )
    Membership.objects.create(user=user, organisation=org)
    return Enquiry.objects.create(
        organisation=org, submitted_by=user, problem="Something hurts.",
        service=service, tier=tier,
    )


def test_enquiries_are_counted_per_service_and_per_tier(catalogue):
    _enquiry(catalogue["implementation"], "Business Setup", org_name="A Co")
    _enquiry(catalogue["implementation"], "Business Setup", org_name="B Co")
    _enquiry(catalogue["implementation"], "Essential Setup", org_name="C Co")
    _enquiry(catalogue["training"], "Professional", org_name="D Co")

    rows = {row["slug"]: row for row in selectors.demand()}

    assert rows["implementation"]["enquiries"] == 3
    assert rows["training"]["enquiries"] == 1
    # Most-asked size first — that is the whole reason to break it down.
    assert rows["implementation"]["tiers"][0] == {"tier": "Business Setup", "count": 2}


def test_an_enquiry_with_no_service_is_counted_not_dropped(catalogue):
    """
    Describing it in your own words is an ordinary route, not a gap. Hiding
    these would make this screen disagree with the queue it summarises.
    """
    _enquiry(None, org_name="Wordy Co")
    _enquiry(catalogue["training"], "Essential", org_name="Picky Co")

    rows = selectors.demand()
    unattributed = [r for r in rows if not r["is_attributed"]]

    assert len(unattributed) == 1
    assert unattributed[0]["enquiries"] == 1
    assert sum(r["enquiries"] for r in rows) == 2


def test_a_tier_nobody_chose_gets_its_own_bucket(catalogue):
    """Blank is not folded into the first tier, which would invent a choice."""
    _enquiry(catalogue["training"], "", org_name="Vague Co")

    rows = {row["slug"]: row for row in selectors.demand()}
    assert rows["training"]["tiers"] == [{"tier": "No size chosen", "count": 1}]


def test_orders_and_money_only_count_once_the_enquiry_converted(catalogue, staff):
    """
    Interest and sales are different numbers. An enquiry that was never
    converted contributes to `enquiries` and to nothing else.
    """
    enquiry = _enquiry(catalogue["implementation"], "Business Setup", org_name="Real Co")
    _enquiry(catalogue["implementation"], "Business Setup", org_name="Browsing Co")

    rows = {row["slug"]: row for row in selectors.demand()}
    assert rows["implementation"]["enquiries"] == 2
    assert rows["implementation"]["orders"] == 0
    assert rows["implementation"]["invoiced_kes"] == "0.00"

    order = services.convert_enquiry(
        enquiry=enquiry, actor=staff, title="Setup", scope="Do the thing."
    )
    contract = services.issue_contract(order=order, actor=staff)
    services.record_signature(
        contract=contract, actor=staff, signed_on=timezone.localdate(),
        signed_by_name="Someone", note="Signed.",
    )
    services.issue_invoice(
        order=order, actor=staff, description="Setup", amount_kes=Decimal("75000.00")
    )

    rows = {row["slug"]: row for row in selectors.demand()}
    assert rows["implementation"]["orders"] == 1
    assert rows["implementation"]["invoiced_kes"] == "75000.00"
    # Billed is not the same as received.
    assert rows["implementation"]["paid_kes"] == "0.00"


def test_a_voided_invoice_stops_counting_as_revenue(catalogue, staff):
    """
    A void withdraws the request for money. Leaving it in would show the
    company as having sold something it withdrew.
    """
    enquiry = _enquiry(catalogue["training"], "Professional", org_name="Refund Co")
    order = services.convert_enquiry(
        enquiry=enquiry, actor=staff, title="Course", scope="Teach."
    )
    contract = services.issue_contract(order=order, actor=staff)
    services.record_signature(
        contract=contract, actor=staff, signed_on=timezone.localdate(),
        signed_by_name="Someone", note="Signed.",
    )
    invoice = services.issue_invoice(
        order=order, actor=staff, description="Course", amount_kes=Decimal("35000.00")
    )

    rows = {row["slug"]: row for row in selectors.demand()}
    assert rows["training"]["invoiced_kes"] == "35000.00"

    services.void_invoice(invoice=invoice, actor=staff, reason="Sent in error.")

    rows = {row["slug"]: row for row in selectors.demand()}
    assert rows["training"]["invoiced_kes"] == "0.00"
    # The order still happened, so the conversion still counts.
    assert rows["training"]["orders"] == 1


def test_the_busiest_service_is_first(catalogue):
    _enquiry(catalogue["training"], "Essential", org_name="One Co")
    for i in range(3):
        _enquiry(catalogue["implementation"], "Essential Setup", org_name=f"Many{i} Co")

    assert selectors.demand()[0]["slug"] == "implementation"


def test_a_client_cannot_read_what_the_company_is_selling(client, catalogue):
    """It is not client data, but it is not theirs to see either."""
    org = Organisation.objects.create(name="Outsider Co")
    user = User.objects.create_user(
        email="outsider@example.com", password=PASSWORD,
        email_verified_at=timezone.now(),
    )
    Membership.objects.create(user=user, organisation=org)

    client.force_login(user)
    assert client.get(reverse("ops-demand")).status_code == 403


def test_staff_get_the_breakdown_over_http(client, staff, catalogue):
    _enquiry(catalogue["implementation"], "Business Setup", org_name="Http Co")

    client.force_login(staff)
    body = client.get(reverse("ops-demand")).json()

    row = next(r for r in body["demand"] if r["slug"] == "implementation")
    assert row["enquiries"] == 1
    assert row["tiers"] == [{"tier": "Business Setup", "count": 1}]


def test_money_crosses_the_api_as_a_string(client, staff, catalogue):
    """
    DRF's JSON encoder turns a Decimal into a FLOAT, and this file has spent
    every other test insisting money never goes through one. A total that
    arrives as 75000.00000000001 is a total somebody will eventually paste into
    a spreadsheet.
    """
    _enquiry(catalogue["implementation"], "Business Setup", org_name="Money Co")

    client.force_login(staff)
    raw = client.get(reverse("ops-demand")).content.decode()

    assert '"invoiced_kes": "0.00"' in raw or '"invoiced_kes":"0.00"' in raw, raw
