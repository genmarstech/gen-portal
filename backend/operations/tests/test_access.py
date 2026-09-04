"""
Who may reach the operations API.

═══════════════════════════════════════════════════════════════════════════════
THE TEST THAT MATTERS IS test_no_client_account_reaches_any_operations_endpoint.

Every other test in this repository can fail and cost a bug. That one failing
means one client can read every other client's enquiries, budgets and scopes —
a confidentiality breach under Charter 05 §V, and the kind that is silent:
nothing errors, the wrong data simply appears on a page.

It enumerates the URLconf rather than listing paths by hand, so an endpoint
added later is covered the day it is added rather than the day someone
remembers to add it here.
═══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import pytest
from django.urls import reverse
from django.utils import timezone

from accounts.models import Organisation, User
from portal.models import Enquiry, Order

pytestmark = pytest.mark.django_db

PASSWORD = "correct-horse-battery"


@pytest.fixture
def staff() -> User:
    return User.objects.create_user(
        email="ops@genmars.co.ke",
        password=PASSWORD,
        full_name="Ops Person",
        # Founder — the widest surface, so "a client cannot reach this"
        # is tested against every route rather than only the shared ones.
        is_staff=True,
        staff_role=User.StaffRole.FOUNDER,
        # A field, not the `is_email_verified` property that reads it.
        email_verified_at=timezone.now(),
    )


@pytest.fixture
def client_user() -> User:
    return User.objects.create_user(
        email="client@example.com",
        password=PASSWORD,
        full_name="A Client",
        email_verified_at=timezone.now(),
    )


@pytest.fixture
def enquiry(client_user) -> Enquiry:
    org = Organisation.objects.create(name="Client Co")
    return Enquiry.objects.create(
        organisation=org,
        submitted_by=client_user,
        problem="We reconcile M-Pesa by hand and it takes two days a week.",
        timeline="Within three months",
        budget_range="KES 250,000 - 500,000",
    )


def operations_urls(enquiry: Enquiry, reference: str = "GM-2026-0001") -> list[str]:
    """Every route this app publishes, built from the URLconf itself."""
    return [
        reverse("ops-overview"),
        reverse("ops-staff"),
        reverse("ops-enquiries"),
        reverse("ops-enquiry", args=[enquiry.pk]),
        reverse("ops-convert", args=[enquiry.pk]),
        reverse("ops-orders"),
        reverse("ops-order", args=[reference]),
        reverse("ops-order-notes", args=[reference]),
        reverse("ops-note-publish", args=[reference, 1]),
        reverse("ops-order-milestones", args=[reference]),
        reverse("ops-milestone", args=[reference, 1]),
        # engineering delivery
        reverse("ops-delivery"),
        reverse("ops-delivery-backfill"),
        reverse("ops-gate", args=[reference, 1]),
        reverse("ops-blockers", args=[reference]),
        reverse("ops-blocker", args=[reference, 1]),
        # client accounts
        reverse("ops-organisations"),
        reverse("ops-org-members", args=[1]),
        reverse("ops-membership", args=[1]),
        # services and contracts
        reverse("ops-services"),
        reverse("ops-service", args=[1]),
        reverse("ops-contracts", args=[reference]),
        reverse("ops-contract-sign", args=[reference, 1]),
        reverse("ops-contract-void", args=[reference, 1]),
        # the team itself
        reverse("ops-staff-member", args=[1]),
        # invoicing
        reverse("ops-invoices", args=[reference]),
        reverse("ops-invoice-payment", args=[reference, 1]),
        reverse("ops-invoice-void", args=[reference, 1]),
        # invoicing, flat — reachable without knowing the order, and for
        # direct invoices there is no order to know.
        reverse("ops-all-invoices"),
        reverse("ops-invoice-payments", args=[1]),
        reverse("ops-invoice-void-flat", args=[1]),
        reverse("ops-billing-profile"),
        reverse("ops-notifications"),
        reverse("ops-demand"),
        # the workroom
        reverse("ops-clock"),
        reverse("ops-timesheet"),
        reverse("ops-decisions"),
        reverse("ops-decision", args=[1]),
        reverse("ops-incidents"),
        reverse("ops-incident", args=[1]),
        reverse("ops-incident-status", args=[1]),
        reverse("ops-tiers"),
        reverse("ops-tier-price", args=[1]),
        reverse("ops-activity"),
        reverse("ops-offers"),
        reverse("ops-offer-action", args=[1]),
        reverse("ops-tasks"),
        reverse("ops-task", args=[1]),
        reverse("ops-systems"),
        reverse("ops-system-events"),
        reverse("ops-system", args=["a-system"]),
        reverse("ops-system-keys", args=["a-system"]),
        reverse("ops-tickets"),
        reverse("ops-ticket", args=["GM-SUP-2026-0001"]),
        reverse("ops-ticket-reply", args=["GM-SUP-2026-0001"]),
        reverse("ops-security-check", args=["a-system", 1]),
    ]


def test_no_client_account_reaches_any_operations_endpoint(client, client_user, enquiry):
    """
    A verified, signed-in CLIENT gets 403 everywhere here.

    Not 404, not an empty list — 403. An empty list would be a correct-looking
    answer produced by a broken gate, and the next endpoint that forgets to
    filter would return real rows.
    """
    client.force_login(client_user)
    for url in operations_urls(enquiry):
        for method in ("get", "post", "patch"):
            response = getattr(client, method)(url, content_type="application/json")
            assert response.status_code == 403, f"{method.upper()} {url} -> {response.status_code}"


def test_anonymous_reaches_nothing(client, enquiry):
    for url in operations_urls(enquiry):
        assert client.get(url).status_code == 403, url


def test_every_operations_route_is_covered_by_the_test_above(enquiry):
    """
    The enumeration must not drift.

    If someone adds a route to operations/urls.py and not to
    `operations_urls()`, the access test silently stops covering it — it would
    still pass, while the new endpoint is wide open. This fails instead.
    """
    from operations import urls as ops_urls

    named = {p.name for p in ops_urls.urlpatterns}
    covered = {
        "ops-overview", "ops-staff", "ops-enquiries", "ops-enquiry", "ops-convert",
        "ops-orders", "ops-order", "ops-order-notes", "ops-note-publish",
        "ops-order-milestones", "ops-milestone",
        "ops-delivery", "ops-delivery-backfill", "ops-gate", "ops-blockers",
        "ops-blocker",
        "ops-organisations", "ops-org-members", "ops-membership",
        "ops-services", "ops-service", "ops-contracts", "ops-contract-sign",
        "ops-contract-void",
        "ops-staff-member",
        "ops-invoices", "ops-invoice-payment", "ops-invoice-void",
        "ops-all-invoices", "ops-invoice-payments", "ops-invoice-void-flat",
        "ops-billing-profile",
        "ops-notifications", "ops-demand",
        "ops-clock", "ops-timesheet", "ops-decisions", "ops-decision",
        "ops-incidents", "ops-incident", "ops-incident-status",
        "ops-tiers", "ops-tier-price", "ops-activity",
        "ops-offers", "ops-offer-action", "ops-tasks", "ops-task",
        "ops-systems", "ops-system", "ops-system-keys", "ops-system-events",
        "ops-tickets", "ops-ticket", "ops-ticket-reply",
        "ops-security-check",
    }
    missing = named - covered
    assert not missing, (
        f"operations/urls.py publishes {sorted(missing)}, which the access test "
        "does not exercise. Add them to operations_urls() and to `covered`."
    )


def test_staff_reach_the_queue(client, staff, enquiry):
    client.force_login(staff)
    response = client.get(reverse("ops-enquiries"))
    assert response.status_code == 200
    assert len(response.json()["enquiries"]) == 1


def test_staff_browsing_the_client_portal_still_see_only_their_memberships(
    client, staff, enquiry
):
    """
    `is_staff` must not become a skeleton key for the CLIENT api.

    portal/selectors.py says "is_staff grants NOTHING here" and that has to stay
    true now there is a staff surface next to it. A staff account with no
    membership sees no orders in the client portal, however much it can see in
    operations.
    """
    Order.objects.create(
        organisation=enquiry.organisation,
        reference="GM-2026-0001",
        title="Something",
        scope="Some scope",
        contact=staff,
    )
    client.force_login(staff)

    assert client.get(reverse("ops-orders")).json()["orders"], "staff see it in ops"
    assert client.get(reverse("order-list")).json()["orders"] == [], (
        "staff must NOT see it in the client portal without a membership"
    )
