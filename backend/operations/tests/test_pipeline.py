"""
Enquiry → Order, and the weekly note.

These cover the writes that create commitments. An order is a fixed scope at a
fixed price (Charter 05 §I) and a published note is something the client is
entitled to rely on (§III), so the interesting cases here are all refusals:
converting twice, converting a decline, publishing over a published note.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.urls import reverse
from django.utils import timezone

from accounts.models import Organisation, User
from operations import services
from portal.models import Enquiry, Milestone, Order, ProgressNote

pytestmark = pytest.mark.django_db

PASSWORD = "correct-horse-battery"


@pytest.fixture
def staff() -> User:
    return User.objects.create_user(
        email="ops@genmars.co.ke", password=PASSWORD, full_name="Ops",
        is_staff=True, email_verified_at=timezone.now(),
    )


@pytest.fixture
def enquiry() -> Enquiry:
    org = Organisation.objects.create(name="Client Co")
    submitter = User.objects.create_user(
        email="client@example.com", password=PASSWORD, full_name="A Client",
        email_verified_at=timezone.now(),
    )
    return Enquiry.objects.create(
        organisation=org, submitted_by=submitter,
        problem="Reconciling M-Pesa by hand, two days a week.",
    )


@pytest.fixture
def signed_in(client, staff):
    client.force_login(staff)
    return client


# ── references ───────────────────────────────────────────────────────────────


def test_references_are_sequential_within_a_year(enquiry, staff):
    assert services.next_reference(dt.date(2026, 5, 1)) == "GM-2026-0001"
    services.convert_enquiry(
        enquiry=enquiry, actor=staff, title="First", scope="Do the thing."
    )
    assert services.next_reference() == "GM-%s-0002" % timezone.localdate().year


def test_references_restart_each_year(enquiry, staff):
    Order.objects.create(
        organisation=enquiry.organisation, reference="GM-2025-0009",
        title="Last year", scope="x", contact=staff,
    )
    assert services.next_reference(dt.date(2026, 1, 2)) == "GM-2026-0001"


# ── conversion ───────────────────────────────────────────────────────────────


def test_converting_creates_the_order_and_links_it_back(signed_in, enquiry):
    response = signed_in.post(
        reverse("ops-convert", args=[enquiry.pk]),
        {"title": "Reconciliation tool", "scope": "Match M-Pesa to invoices.",
         "exclusions": "No accounting integration."},
        content_type="application/json",
    )
    assert response.status_code == 201, response.json()
    body = response.json()

    enquiry.refresh_from_db()
    assert enquiry.status == Enquiry.Status.CONVERTED
    assert enquiry.converted_to.reference == body["reference"]
    # Charter 01 §V — nothing ships without a named owner.
    assert enquiry.decided_by is not None
    assert enquiry.decided_at is not None
    # It belongs to the client who asked, not to whoever converted it.
    assert body["organisation"] == "Client Co"
    assert body["status"] == Order.Status.SCOPING


def test_an_enquiry_converts_only_once(signed_in, enquiry):
    payload = {"title": "One", "scope": "Some scope."}
    first = signed_in.post(reverse("ops-convert", args=[enquiry.pk]), payload,
                           content_type="application/json")
    assert first.status_code == 201

    second = signed_in.post(reverse("ops-convert", args=[enquiry.pk]), payload,
                            content_type="application/json")
    assert second.status_code == 400
    assert "already order" in second.json()["detail"]
    # The refusal is the point: two orders for one piece of work means two sets
    # of milestones to invoice against.
    assert Order.objects.count() == 1


def test_a_declined_enquiry_does_not_convert(signed_in, enquiry, staff):
    services.decide_enquiry(
        enquiry=enquiry, actor=staff, status=Enquiry.Status.DECLINED,
        note="Outside what we do.",
    )
    response = signed_in.post(
        reverse("ops-convert", args=[enquiry.pk]),
        {"title": "x", "scope": "y"}, content_type="application/json",
    )
    assert response.status_code == 400
    assert Order.objects.count() == 0


def test_an_order_cannot_be_created_without_a_scope(signed_in, enquiry):
    response = signed_in.post(
        reverse("ops-convert", args=[enquiry.pk]),
        {"title": "No scope", "scope": "   "}, content_type="application/json",
    )
    assert response.status_code == 400
    assert response.json()["field"] == "scope"


def test_the_named_contact_cannot_be_a_client(enquiry, staff):
    """Order.contact is the client's escalation path under Charter 05 §I.
    Pointing it at the client makes them their own escalation path."""
    with pytest.raises(services.OperationsError):
        services.convert_enquiry(
            enquiry=enquiry, actor=staff, title="x", scope="y",
            contact=enquiry.submitted_by,
        )


# ── triage ───────────────────────────────────────────────────────────────────


def test_declining_requires_a_reason(signed_in, enquiry):
    response = signed_in.post(
        reverse("ops-enquiry", args=[enquiry.pk]),
        {"status": "declined", "note": ""}, content_type="application/json",
    )
    assert response.status_code == 400
    assert response.json()["field"] == "outcome_note"


def test_converted_cannot_be_set_by_hand(signed_in, enquiry):
    """
    Otherwise an enquiry can be marked converted with no order behind it —
    exactly the dead end `converted_to` was added to close.
    """
    response = signed_in.post(
        reverse("ops-enquiry", args=[enquiry.pk]),
        {"status": "converted"}, content_type="application/json",
    )
    assert response.status_code == 400
    enquiry.refresh_from_db()
    assert enquiry.status == Enquiry.Status.NEW


# ── weekly notes ─────────────────────────────────────────────────────────────


@pytest.fixture
def order(enquiry, staff) -> Order:
    return services.convert_enquiry(
        enquiry=enquiry, actor=staff, title="Work", scope="Do the thing."
    )


def test_a_draft_note_is_not_visible_to_the_client(signed_in, order):
    from portal.selectors import published_notes_for

    signed_in.post(
        reverse("ops-order-notes", args=[order.reference]),
        {"week_of": "2026-09-07", "body": "Half written."},
        content_type="application/json",
    )
    assert order.notes.count() == 1
    assert published_notes_for(order).count() == 0, "a draft is not a promise"


def test_publishing_makes_it_visible_and_then_immutable(signed_in, order):
    from portal.selectors import published_notes_for

    created = signed_in.post(
        reverse("ops-order-notes", args=[order.reference]),
        {"week_of": "2026-09-07", "body": "Shipped the import."},
        content_type="application/json",
    ).json()

    signed_in.post(
        reverse("ops-note-publish", args=[order.reference, created["id"]]),
        content_type="application/json",
    )
    assert published_notes_for(order).count() == 1

    # Charter 05 §III — the client is entitled to rely on what they were told.
    again = signed_in.post(
        reverse("ops-order-notes", args=[order.reference]),
        {"week_of": "2026-09-07", "body": "Actually, no."},
        content_type="application/json",
    )
    assert again.status_code == 400
    assert ProgressNote.objects.get(pk=created["id"]).body == "Shipped the import."


def test_a_draft_can_still_be_rewritten(signed_in, order):
    first = signed_in.post(
        reverse("ops-order-notes", args=[order.reference]),
        {"week_of": "2026-09-07", "body": "Typo heer."},
        content_type="application/json",
    ).json()
    second = signed_in.post(
        reverse("ops-order-notes", args=[order.reference]),
        {"week_of": "2026-09-07", "body": "Typo here."},
        content_type="application/json",
    ).json()
    assert first["id"] == second["id"], "same week is the same note, not a conflict"
    assert ProgressNote.objects.get(pk=first["id"]).body == "Typo here."


# ── milestones ───────────────────────────────────────────────────────────────


def test_marking_a_milestone_paid_stamps_the_time_server_side(signed_in, order):
    created = signed_in.post(
        reverse("ops-order-milestones", args=[order.reference]),
        {"name": "Deposit", "amount_kes": "150000.00", "position": 1},
        content_type="application/json",
    ).json()

    signed_in.patch(
        reverse("ops-milestone", args=[order.reference, created["id"]]),
        {"status": "paid"}, content_type="application/json",
    )
    milestone = Milestone.objects.get(pk=created["id"])
    assert milestone.status == Milestone.Status.PAID
    # A paid date the browser can choose is one nobody can reconcile against a
    # bank statement.
    assert milestone.paid_at is not None


def test_money_crosses_the_api_as_a_string(signed_in, order):
    created = signed_in.post(
        reverse("ops-order-milestones", args=[order.reference]),
        {"name": "Final", "amount_kes": "1250000.50", "position": 2},
        content_type="application/json",
    ).json()
    assert created["amount_kes"] == "1250000.50"
    assert isinstance(created["amount_kes"], str)


# ── the header numbers ───────────────────────────────────────────────────────


def test_awaiting_note_counts_active_orders_with_no_recent_published_note(
    signed_in, order, staff
):
    """
    Charter 05 §III promises a written update every week. A non-zero count here
    is that promise slipping, visible before the client notices rather than
    after they ask.
    """
    order.status = Order.Status.ACTIVE
    order.save(update_fields=["status"])

    counts = signed_in.get(reverse("ops-overview")).json()["counts"]
    assert counts["awaiting_note"] == 1

    note = ProgressNote.objects.create(
        order=order, author=staff, week_of=timezone.localdate(), body="On track."
    )
    services.publish_note(note=note)

    counts = signed_in.get(reverse("ops-overview")).json()["counts"]
    assert counts["awaiting_note"] == 0
