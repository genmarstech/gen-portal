"""
Tenant isolation.

The single most important test file in this repository. A client seeing another
client's order is a confidentiality breach under Charter 05 §V, and it is the
failure mode that a permissions bug produces silently — nothing errors, the
wrong data just appears.

Written as a test rather than a code-review note, because a code-review note
does not run in CI.
"""

from __future__ import annotations

import datetime as dt

import pytest

from accounts.models import Membership, Organisation, User
from portal.models import Milestone, Order, ProgressNote
from portal.selectors import orders_for, order_for

pytestmark = pytest.mark.django_db


@pytest.fixture
def staff() -> User:
    return User.objects.create_user(
        email="edwin@genmars.co.ke", password="x" * 12, is_staff=True
    )


@pytest.fixture
def world(staff):
    """Two unrelated clients, each with one order."""
    org_a = Organisation.objects.create(name="Acme Ltd")
    org_b = Organisation.objects.create(name="Beta Ltd")

    user_a = User.objects.create_user(email="a@acme.example", password="x" * 12)
    user_b = User.objects.create_user(email="b@beta.example", password="x" * 12)
    Membership.objects.create(user=user_a, organisation=org_a)
    Membership.objects.create(user=user_b, organisation=org_b)

    order_a = Order.objects.create(
        organisation=org_a, reference="GM-001", title="Acme reconciliation",
        scope="Reconcile M-Pesa against invoices.", contact=staff,
    )
    order_b = Order.objects.create(
        organisation=org_b, reference="GM-002", title="Beta booking",
        scope="Booking system.", contact=staff,
    )
    return {
        "user_a": user_a, "user_b": user_b,
        "org_a": org_a, "org_b": org_b,
        "order_a": order_a, "order_b": order_b,
    }


def test_client_sees_only_their_own_orders(world):
    assert list(orders_for(world["user_a"])) == [world["order_a"]]
    assert list(orders_for(world["user_b"])) == [world["order_b"]]


def test_client_cannot_fetch_another_organisations_order_by_reference(world):
    """
    The obvious attack: change the reference in the URL. Must return nothing,
    not raise a permission error that confirms the order exists.
    """
    assert order_for(world["user_a"], "GM-002") is None
    assert order_for(world["user_b"], "GM-001") is None


def test_client_can_fetch_their_own_order_by_reference(world):
    assert order_for(world["user_a"], "GM-001") == world["order_a"]


def test_user_with_no_membership_sees_nothing():
    """The common early case: signed up, no order yet. Must be empty, not error."""
    orphan = User.objects.create_user(email="new@example.com", password="x" * 12)
    assert list(orders_for(orphan)) == []


def test_progress_notes_do_not_leak_across_organisations(world, staff):
    ProgressNote.objects.create(
        order=world["order_b"], author=staff, body="Beta progress.",
        week_of=dt.date(2026, 8, 24),
    )
    visible = orders_for(world["user_a"]).prefetch_related("notes")
    assert all(n.order.organisation == world["org_a"] for o in visible for n in o.notes.all())


def test_milestones_do_not_leak_across_organisations(world):
    Milestone.objects.create(
        order=world["order_b"], name="Deposit", amount_kes=100000, position=1
    )
    visible = orders_for(world["user_a"]).prefetch_related("milestones")
    assert all(m.order.organisation == world["org_a"] for o in visible for m in o.milestones.all())


def test_membership_in_two_organisations_sees_both(world, staff):
    """A consultant working with two clients is legitimate and must work."""
    Membership.objects.create(user=world["user_a"], organisation=world["org_b"])
    assert set(orders_for(world["user_a"])) == {world["order_a"], world["order_b"]}


def test_staff_flag_alone_grants_no_client_data(world, staff):
    """
    is_staff means Genmars, not "sees everything through the client API".
    Staff use the admin; the client-facing selectors go through membership only.
    """
    assert list(orders_for(staff)) == []


def test_unpublished_notes_are_not_visible_to_clients(world, staff):
    """A draft note is not a promise. Charter 05 §I — the note is what we told them."""
    ProgressNote.objects.create(
        order=world["order_a"], author=staff, body="Draft, not sent.",
        week_of=dt.date(2026, 8, 24),
    )
    order = order_for(world["user_a"], "GM-001")
    assert order is not None
    assert order.notes.filter(published_at__isnull=False).count() == 0
