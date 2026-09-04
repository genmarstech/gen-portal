"""
Telling a client that something on their order changed.

═══════════════════════════════════════════════════════════════════════════════
THE TEST THAT MATTERS IS test_a_scope_change_is_flagged_to_the_client.

Charter 05 §I fixes the scope in writing. A client whose scope changed without
noticing has had that promise quietly broken — and it is the change they are
least likely to go looking for, because nothing about their week tells them to.
═══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from accounts.models import Membership, Organisation, User
from operations import services
from portal import selectors
from portal.models import Contract, Order, OrderSeen, ProgressNote

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
def spa() -> Organisation:
    return Organisation.objects.create(name="Clips Serenity Spa")


@pytest.fixture
def owner(spa) -> User:
    person = User.objects.create_user(
        email="owner@spa.co.ke", password=PASSWORD, full_name="The owner",
        email_verified_at=timezone.now(),
    )
    Membership.objects.create(user=person, organisation=spa)
    return person


@pytest.fixture
def order(spa, staff) -> Order:
    return Order.objects.create(
        organisation=spa, reference="GM-2026-0042", title="Spa website",
        contact=staff, scope="Five-page site with a booking enquiry form.",
    )


def _list(client):
    return client.get(reverse("order-list")).json()["orders"]


# ── what raises a marker ─────────────────────────────────────────────────────


def test_a_scope_change_is_flagged_to_the_client(client, staff, owner, order):
    """
    ═══════════════════════════════════════════════════════════════════════════
    The change they are least likely to go looking for.
    ═══════════════════════════════════════════════════════════════════════════
    """
    client.force_login(owner)
    client.get(reverse("order-detail", args=[order.reference]))  # seen
    assert _list(client)[0]["unseen"] is None

    client.logout()
    client.force_login(staff)
    client.patch(
        reverse("ops-order", args=[order.reference]),
        {"scope": "Five-page site, booking form, and online payments."},
        content_type="application/json",
    )

    client.logout()
    client.force_login(owner)
    assert _list(client)[0]["unseen"] == "Scope changed"


def test_the_reason_is_in_the_clients_words(client, staff, owner, order):
    """
    A bare dot says there is something to find without saying whether it is
    worth finding, and half of them will not go looking.
    """
    client.force_login(owner)
    client.get(reverse("order-detail", args=[order.reference]))

    note = ProgressNote.objects.create(
        order=order, author=staff, week_of=timezone.localdate(), body="Made progress."
    )
    services.publish_note(note=note)

    assert _list(client)[0]["unseen"] == "New progress note"


def test_a_statement_of_work_being_issued_is_flagged(client, staff, owner, order):
    client.force_login(owner)
    client.get(reverse("order-detail", args=[order.reference]))

    services.issue_contract(order=order, actor=staff)
    assert _list(client)[0]["unseen"] == "Statement of work issued"


def test_a_replacement_statement_says_it_is_a_replacement(client, staff, owner, order):
    """
    "Statement of work issued" against a second version would read as the
    first one arriving, which is the opposite of what happened.
    """
    services.issue_contract(order=order, actor=staff)
    client.force_login(owner)
    client.get(reverse("order-detail", args=[order.reference]))

    services.issue_contract(order=order, actor=staff)
    assert "replaced" in _list(client)[0]["unseen"]


def test_an_invoice_is_flagged_on_the_order_it_bills(client, staff, owner, order):
    contract = services.issue_contract(order=order, actor=staff)
    services.record_signature(
        contract=contract, actor=staff, signed_on=timezone.localdate(),
        signed_by_name="The owner",
    )
    client.force_login(owner)
    client.get(reverse("order-detail", args=[order.reference]))

    invoice = services.issue_invoice(
        order=order, actor=staff, description="Deposit", amount_kes=Decimal("50000.00")
    )
    assert invoice.number in _list(client)[0]["unseen"]


def test_an_order_never_opened_counts_as_unseen(client, staff, owner, order):
    """
    A client who has never looked has not seen the scope on it, and that is
    exactly the state the marker exists for.
    """
    services.issue_contract(order=order, actor=staff)
    client.force_login(owner)
    assert _list(client)[0]["unseen"] is not None


# ── what does NOT raise one ──────────────────────────────────────────────────


def test_an_internal_edit_raises_nothing(client, staff, owner, order):
    """
    ═══════════════════════════════════════════════════════════════════════════
    THE RESTRAINT THAT KEEPS THE MARKER MEANINGFUL.

    `updated_at` moves when anything is touched — a target date nudged, a
    status changed, a service reassigned. A badge in front of a client several
    times a week for changes that mean nothing to them is a badge people stop
    seeing.
    ═══════════════════════════════════════════════════════════════════════════
    """
    client.force_login(owner)
    client.get(reverse("order-detail", args=[order.reference]))

    client.logout()
    client.force_login(staff)
    client.patch(
        reverse("ops-order", args=[order.reference]),
        {"status": "active", "target_date": "2026-12-01"},
        content_type="application/json",
    )

    client.logout()
    client.force_login(owner)
    assert _list(client)[0]["unseen"] is None


def test_a_draft_note_raises_nothing(client, staff, owner, order):
    """A draft is not something the client has been told."""
    client.force_login(owner)
    client.get(reverse("order-detail", args=[order.reference]))

    ProgressNote.objects.create(
        order=order, author=staff, week_of=timezone.localdate(), body="Not published.",
    )
    assert _list(client)[0]["unseen"] is None


# ── clearing it ──────────────────────────────────────────────────────────────


def test_opening_the_order_clears_the_marker(client, staff, owner, order):
    services.issue_contract(order=order, actor=staff)
    client.force_login(owner)
    assert _list(client)[0]["unseen"] is not None

    client.get(reverse("order-detail", args=[order.reference]))
    assert _list(client)[0]["unseen"] is None


def test_the_page_still_says_what_changed_on_the_visit_that_clears_it(
    client, staff, owner, order
):
    """
    Read before the visit is stamped. Otherwise opening the page clears the
    marker and the page itself never tells them what it was for.
    """
    services.issue_contract(order=order, actor=staff)
    client.force_login(owner)

    body = client.get(reverse("order-detail", args=[order.reference])).json()
    assert body["unseen"] == "Statement of work issued"


def test_one_persons_visit_does_not_clear_it_for_a_colleague(
    client, staff, owner, spa, order
):
    """
    ═══════════════════════════════════════════════════════════════════════════
    A shared flag would clear the marker for somebody who never looked — worse
    than no marker, because it produces a client who was never told and a
    system that believes they were.
    ═══════════════════════════════════════════════════════════════════════════
    """
    colleague = User.objects.create_user(
        email="manager@spa.co.ke", password=PASSWORD, email_verified_at=timezone.now()
    )
    Membership.objects.create(user=colleague, organisation=spa)

    services.issue_contract(order=order, actor=staff)

    client.force_login(owner)
    client.get(reverse("order-detail", args=[order.reference]))
    assert _list(client)[0]["unseen"] is None

    client.logout()
    client.force_login(colleague)
    assert _list(client)[0]["unseen"] is not None


def test_a_later_change_raises_it_again(client, staff, owner, order):
    client.force_login(owner)
    client.get(reverse("order-detail", args=[order.reference]))

    services.issue_contract(order=order, actor=staff)
    assert _list(client)[0]["unseen"] is not None

    client.get(reverse("order-detail", args=[order.reference]))
    assert _list(client)[0]["unseen"] is None


def test_seeing_an_order_is_one_row_not_a_reading_history(client, owner, order):
    """
    One timestamp, overwritten. A record of when a client opened what is not
    ours to keep and is not what this is for.
    """
    client.force_login(owner)
    for _ in range(3):
        client.get(reverse("order-detail", args=[order.reference]))

    assert OrderSeen.objects.filter(user=owner, order=order).count() == 1


def test_one_client_never_sees_another_clients_marker(client, staff, owner):
    """The seen record is scoped to the user in the prefetch, so it cannot
    read somebody else's."""
    other = Organisation.objects.create(name="Somebody Else Ltd")
    theirs = Order.objects.create(
        organisation=other, reference="GM-2026-0099", title="Theirs",
        contact=staff, scope="Build it.",
    )
    services.issue_contract(order=theirs, actor=staff)

    client.force_login(owner)
    assert [o["reference"] for o in _list(client)] == []


def test_an_unauthenticated_list_is_not_a_crash(client):
    """`unseen` needs a user; without one it must be null rather than an error."""
    assert client.get(reverse("order-list")).status_code in (401, 403)
