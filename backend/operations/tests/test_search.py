"""
The search box.

═══════════════════════════════════════════════════════════════════════════════
THE TEST THAT MATTERS IS test_a_reference_beats_everything_else.

Searching more tables is easy; the hard part is ranking. Somebody pasting
"GM-INV-2026-0004" out of a bank statement wants exactly one row, and a list
ordered by primary key serves them no better than no search at all.
═══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from accounts.models import Membership, Organisation, User
from operations import search as search_module, services
from portal.models import ContactLogEntry, HostingArrangement, Invoice, Order

pytestmark = pytest.mark.django_db

PASSWORD = "correct-horse-battery"


@pytest.fixture
def staff() -> User:
    return User.objects.create_user(
        email="ops@genmars.co.ke", password=PASSWORD, full_name="Ops Person",
        is_staff=True, staff_role=User.StaffRole.FOUNDER,
        email_verified_at=timezone.now(),
    )


@pytest.fixture
def spa() -> Organisation:
    return Organisation.objects.create(name="Clips Serenity Spa")


def _find(client, q):
    return client.get(reverse("ops-search"), {"q": q}).json()["results"]


# ── finding things ───────────────────────────────────────────────────────────


def test_a_client_is_found_by_name(client, staff, spa):
    client.force_login(staff)
    hits = _find(client, "serenity")
    assert hits[0]["kind"] == "Client"
    assert hits[0]["href"] == f"/clients/{spa.pk}"


def test_an_order_is_found_by_reference_title_or_scope(client, staff, spa):
    Order.objects.create(
        organisation=spa, reference="GM-2026-0042", title="Spa website",
        contact=staff, scope="Five-page site with a booking enquiry form.",
    )
    client.force_login(staff)

    for query in ("GM-2026-0042", "Spa website", "booking enquiry"):
        hits = [h for h in _find(client, query) if h["kind"] == "Order"]
        assert hits, query
        assert hits[0]["href"] == "/orders/GM-2026-0042"


def test_a_conversation_is_found_by_something_said_in_the_middle_of_it(
    client, staff, spa
):
    """
    The thing somebody half-remembers is usually a phrase from the middle of a
    call, not the one-line summary.
    """
    ContactLogEntry.objects.create(
        organisation=spa, channel="whatsapp", direction="inbound",
        summary="Called about the site",
        detail="She mentioned wanting card payments before December.",
    )
    client.force_login(staff)
    hits = [h for h in _find(client, "card payments") if h["kind"] == "Conversation"]
    assert hits
    assert hits[0]["href"] == f"/clients/{spa.pk}"


def test_a_domain_is_found(client, staff, spa):
    HostingArrangement.objects.create(
        organisation=spa, kind="domain", identifier="clipsserenityspa.co.ke",
    )
    client.force_login(staff)
    hits = [h for h in _find(client, "clipsserenityspa") if h["kind"] == "Hosting"]
    assert hits
    assert hits[0]["exact"] is True


def test_a_decision_is_found_by_its_reasoning(client, staff):
    services.record_decision(
        actor=staff, title="Bill in KES only",
        context="Two of our first four enquiries asked to be billed in USD.",
        decision="Every invoice is issued in KES.",
    )
    client.force_login(staff)
    hits = [h for h in _find(client, "billed in USD") if h["kind"] == "Decision"]
    assert hits


def test_a_colleague_is_found(client, staff):
    client.force_login(staff)
    hits = [h for h in _find(client, "Ops Person") if h["kind"] == "Colleague"]
    assert hits
    assert hits[0]["sublabel"] == "Founder"


def test_a_retired_arrangement_is_not_offered(client, staff, spa):
    """Search answers "what is there", not "what was there"."""
    HostingArrangement.objects.create(
        organisation=spa, kind="domain", identifier="old.co.ke",
        retired_at=timezone.now(),
    )
    client.force_login(staff)
    assert not [h for h in _find(client, "old.co.ke") if h["kind"] == "Hosting"]


def test_a_finished_task_is_not_offered(client, staff, spa):
    from portal.models import Task

    Task.objects.create(
        title="Chase the logo files", assignee=staff, assigned_by=staff,
        status=Task.Status.DONE,
    )
    client.force_login(staff)
    assert not [h for h in _find(client, "logo files") if h["kind"] == "Task"]


# ── ranking ──────────────────────────────────────────────────────────────────


def test_a_reference_beats_everything_else(client, staff, spa):
    """
    ═══════════════════════════════════════════════════════════════════════════
    Somebody pasting a number out of a bank statement wants exactly one row.
    ═══════════════════════════════════════════════════════════════════════════
    """
    order = Order.objects.create(
        organisation=spa, reference="GM-2026-0042", title="Spa website",
        contact=staff, scope="Build it.",
    )
    # Prose that also matches, from a different type.
    ContactLogEntry.objects.create(
        organisation=spa, channel="call", direction="inbound",
        summary="Talked about GM-2026-0042 and the timeline",
    )

    client.force_login(staff)
    hits = _find(client, "GM-2026-0042")

    assert hits[0]["kind"] == "Order"
    assert hits[0]["exact"] is True
    assert hits[0]["href"] == f"/orders/{order.reference}"


def test_the_order_is_stable_between_keystrokes(client, staff, spa):
    """A list that reshuffles as you type is one you cannot click."""
    for n in range(3):
        Order.objects.create(
            organisation=spa, reference=f"GM-2026-000{n}", title=f"Booking work {n}",
            contact=staff, scope="Build it.",
        )
    client.force_login(staff)
    first = [h["label"] for h in _find(client, "Booking")]
    second = [h["label"] for h in _find(client, "Booking")]
    assert first == second


def test_no_one_type_can_bury_the_others(client, staff, spa):
    """
    Without a cap, one client with eighty conversations buries every order and
    invoice under a wall of chat summaries, and the box looks broken.
    """
    for n in range(20):
        ContactLogEntry.objects.create(
            organisation=spa, channel="call", direction="inbound",
            summary=f"Booking call number {n}",
        )
    Order.objects.create(
        organisation=spa, reference="GM-2026-0042", title="Booking system",
        contact=staff, scope="Build it.",
    )

    client.force_login(staff)
    hits = _find(client, "Booking")
    assert len([h for h in hits if h["kind"] == "Conversation"]) <= 5
    assert any(h["kind"] == "Order" for h in hits)


# ── what it will not do ──────────────────────────────────────────────────────


def test_one_character_searches_nothing(client, staff, spa):
    """A full-table scan across a dozen models to produce something unreadable."""
    client.force_login(staff)
    assert _find(client, "a") == []
    assert _find(client, "") == []


def test_amounts_are_not_searchable(client, staff, spa):
    """
    A search for "5000" that returns invoices looks helpful and is not: it
    matches 5000, 15000 and 50000 with equal confidence, and money is the one
    place a nearly-right answer is worse than none.
    """
    order = Order.objects.create(
        organisation=spa, reference="GM-2026-0042", title="Spa website",
        contact=staff, scope="Build it.",
    )
    Invoice.objects.create(
        number="GM-INV-2026-0001", organisation=spa, order=order,
        description="Deposit", amount_kes=Decimal("50000.00"),
        issued_on=timezone.localdate(), issued_by=staff,
    )
    client.force_login(staff)
    assert not [h for h in _find(client, "50000") if h["kind"] == "Invoice"]
    # And it is still findable the way people actually look for it.
    assert [h for h in _find(client, "GM-INV-2026-0001") if h["kind"] == "Invoice"]


def test_a_client_account_cannot_search(client, spa):
    """
    Every row this touches is internal — the contact log especially, which is
    written honestly because nobody outside Genmars reads it.
    """
    outsider = User.objects.create_user(
        email="client@example.com", password=PASSWORD, email_verified_at=timezone.now()
    )
    Membership.objects.create(user=outsider, organisation=spa)
    client.force_login(outsider)
    assert client.get(reverse("ops-search"), {"q": "serenity"}).status_code == 403


# ── filters ──────────────────────────────────────────────────────────────────


@pytest.fixture
def a_bit_of_everything(staff, spa):
    Order.objects.create(
        organisation=spa, reference="GM-2026-0042", title="Booking system",
        contact=staff, scope="Build it.",
    )
    ContactLogEntry.objects.create(
        organisation=spa, channel="call", direction="inbound",
        summary="Booking call with the owner",
    )
    HostingArrangement.objects.create(
        organisation=spa, kind="domain", identifier="booking.example.co.ke",
    )
    return spa


def test_the_filter_row_lists_only_kinds_that_matched(client, staff, a_bit_of_everything):
    """
    A filter for something with no results is a control that does nothing, and
    a row of them is a row you learn to ignore.
    """
    client.force_login(staff)
    body = client.get(reverse("ops-search"), {"q": "booking"}).json()
    kinds = {row["kind"]: row["count"] for row in body["kinds"]}

    assert kinds["Order"] == 1
    assert kinds["Conversation"] == 1
    assert kinds["Hosting"] == 1
    assert "Invoice" not in kinds


def test_the_filter_row_keeps_a_fixed_order(client, staff, a_bit_of_everything):
    """
    A row that reorders itself between searches is one you have to read every
    time rather than aim at.
    """
    client.force_login(staff)
    body = client.get(reverse("ops-search"), {"q": "booking"}).json()
    order = [row["kind"] for row in body["kinds"]]
    assert order == sorted(order, key=lambda k: search_module.KINDS.index(k))


def test_picking_a_filter_narrows_the_results(client, staff, a_bit_of_everything):
    client.force_login(staff)
    body = client.get(
        reverse("ops-search"), {"q": "booking", "kind": "Order"}
    ).json()

    assert {hit["kind"] for hit in body["results"]} == {"Order"}


def test_the_counts_survive_a_filter_being_applied(client, staff, a_bit_of_everything):
    """
    ═══════════════════════════════════════════════════════════════════════════
    Otherwise picking "Order" makes every other filter read as zero, and the
    way back disappears.
    ═══════════════════════════════════════════════════════════════════════════
    """
    client.force_login(staff)
    body = client.get(
        reverse("ops-search"), {"q": "booking", "kind": "Order"}
    ).json()

    kinds = {row["kind"]: row["count"] for row in body["kinds"]}
    assert kinds["Conversation"] == 1
    assert kinds["Hosting"] == 1


def test_an_unknown_filter_returns_nothing_rather_than_everything(
    client, staff, a_bit_of_everything
):
    """Failing open on a filter shows more than was asked for, which reads as
    the filter being broken."""
    client.force_login(staff)
    body = client.get(
        reverse("ops-search"), {"q": "booking", "kind": "Nonsense"}
    ).json()
    assert body["results"] == []


def test_the_counts_never_promise_more_than_the_list_shows(client, staff, spa):
    """
    Per-type results are capped. A count of 40 above a list of 5 is a promise
    the list does not keep, and somebody spends a while looking for the rest.
    """
    for n in range(20):
        ContactLogEntry.objects.create(
            organisation=spa, channel="call", direction="inbound",
            summary=f"Booking call number {n}",
        )
    client.force_login(staff)
    body = client.get(reverse("ops-search"), {"q": "booking"}).json()

    counted = next(r["count"] for r in body["kinds"] if r["kind"] == "Conversation")
    shown = len([h for h in body["results"] if h["kind"] == "Conversation"])
    assert counted == shown
