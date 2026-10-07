"""
The bin, and the two things it is for.

═══════════════════════════════════════════════════════════════════════════════
THE ONE THAT MATTERS: test_an_order_that_cannot_be_deleted_can_be_binned.

Deleting refuses an order that has been signed for or billed for, and that
refusal is right and absolute. It also left test data made by exercising the
whole flow permanently in the working lists, which is what the bin fixes.

Trashing destroys nothing — the row, the contract, the invoice and the money
all stay — so none of the reasons to refuse a deletion apply to it.
═══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from accounts.models import Organisation, User
from operations import selectors, services
from portal.models import ActivityLog, Contract, Invoice, Order

pytestmark = pytest.mark.django_db

PASSWORD = "correct-horse-battery"


def _user(email: str, role: str) -> User:
    return User.objects.create_user(
        email=email,
        password=PASSWORD,
        full_name=email.split("@")[0].title(),
        is_staff=True,
        staff_role=role,
        email_verified_at=timezone.now(),
    )


@pytest.fixture
def delivery() -> User:
    return _user("delivery@genmars.co.ke", User.StaffRole.DELIVERY)


@pytest.fixture
def org() -> Organisation:
    return Organisation.objects.create(name="Riverside Dental")


@pytest.fixture
def contact() -> User:
    return User.objects.create_user(
        email="client@riverside.example",
        password=PASSWORD,
        full_name="A Client",
        email_verified_at=timezone.now(),
    )


@pytest.fixture
def order(org, contact) -> Order:
    return Order.objects.create(
        organisation=org,
        reference="GM-2026-0099",
        title="A test order",
        scope="Something to bin.",
        contact=contact,
    )


# ── the default manager ─────────────────────────────────────────────────────


def test_a_binned_order_leaves_the_lists(order, delivery):
    assert Order.objects.count() == 1
    services.trash_order(actor=delivery, order=order, reason="Made while testing")
    assert Order.objects.count() == 0
    assert Order.all_objects.count() == 1


def test_it_leaves_the_selectors_too(order, delivery):
    """
    The point of filtering the DEFAULT manager: fifteen call sites stop
    showing it without one of them being edited.
    """
    assert selectors.orders().count() == 1
    assert selectors.order("GM-2026-0099") is not None

    services.trash_order(actor=delivery, order=order)

    assert selectors.orders().count() == 0
    assert selectors.order("GM-2026-0099") is None
    # But the bin's own screens can still reach it, or nothing could restore it.
    assert selectors.order("GM-2026-0099", include_trashed=True) is not None


def test_a_reference_is_never_reused_after_binning(order, delivery):
    """
    ⚠ THE SUBTLE ONE. next_reference counts the orders in the year. Counted
      through the default manager, binning GM-2026-0099 hands the next order
      the same number back.
    """
    services.trash_order(actor=delivery, order=order)
    assert services.next_reference(date(2026, 1, 5)) != "GM-2026-0099"


# ── what it is for ──────────────────────────────────────────────────────────


def test_an_order_that_cannot_be_deleted_can_be_binned(order, delivery, org):
    """See this file's banner."""
    Contract.objects.create(
        order=order, title="SOW", scope="Agreed.", status=Contract.Status.SIGNED
    )
    Invoice.objects.create(
        number="GM-INV-2026-0099",
        organisation=org,
        order=order,
        description="Phase one",
        amount_kes=Decimal("50000"),
        issued_on=date(2026, 10, 1),
    )

    assert services.order_deletion_preview(order)["may_delete"] is False

    services.trash_order(actor=delivery, order=order, reason="Test data")

    order.refresh_from_db()
    assert order.is_trashed
    # Nothing was destroyed.
    assert Contract.objects.filter(order_id=order.pk).count() == 1
    assert Invoice.objects.filter(order_id=order.pk).count() == 1


def test_restoring_puts_it_back_exactly(order, delivery):
    services.trash_order(actor=delivery, order=order, reason="Oops")
    services.restore_order(actor=delivery, order=Order.all_objects.get(pk=order.pk))

    order.refresh_from_db()
    assert order.is_trashed is False
    assert order.trash_reason == ""
    assert order.trashed_by is None
    assert Order.objects.count() == 1


def test_binning_twice_changes_nothing(order, delivery):
    services.trash_order(actor=delivery, order=order, reason="First")
    first = Order.all_objects.get(pk=order.pk).trashed_at
    services.trash_order(actor=delivery, order=Order.all_objects.get(pk=order.pk), reason="Second")
    assert Order.all_objects.get(pk=order.pk).trashed_at == first


# ── the log ─────────────────────────────────────────────────────────────────


def test_both_acts_are_logged_with_the_reason(order, delivery):
    services.trash_order(actor=delivery, order=order, reason="Made while testing")
    entry = ActivityLog.objects.get(action=ActivityLog.Action.ORDER_TRASHED)
    assert entry.subject == "GM-2026-0099"
    assert "Made while testing" in entry.summary

    services.restore_order(actor=delivery, order=Order.all_objects.get(pk=order.pk))
    assert ActivityLog.objects.filter(
        action=ActivityLog.Action.ORDER_RESTORED, subject="GM-2026-0099"
    ).exists()


# ── over the wire ───────────────────────────────────────────────────────────


def test_any_member_of_staff_may_bin_and_restore(client, order, delivery):
    """
    Wider than deleting, on purpose: this can be taken back and that cannot.
    """
    assert client.login(email=delivery.email, password=PASSWORD)

    response = client.post(
        reverse("ops-order-trash", args=[order.reference]),
        data={"reason": "Test order"},
        content_type="application/json",
    )
    assert response.status_code == 200
    assert response.json()["trashed"] is True

    bin_page = client.get(reverse("ops-trash"))
    assert bin_page.status_code == 200
    assert [o["reference"] for o in bin_page.json()["orders"]] == ["GM-2026-0099"]
    assert bin_page.json()["orders"][0]["reason"] == "Test order"

    back = client.post(
        reverse("ops-order-trash", args=[order.reference]),
        data={"restore": True},
        content_type="application/json",
    )
    assert back.status_code == 200
    assert back.json()["trashed"] is False
    assert client.get(reverse("ops-trash")).json()["orders"] == []


def test_the_order_list_stops_showing_it(client, order, delivery):
    assert client.login(email=delivery.email, password=PASSWORD)
    assert len(client.get(reverse("ops-orders")).json()["orders"]) == 1

    client.post(
        reverse("ops-order-trash", args=[order.reference]),
        data={},
        content_type="application/json",
    )
    assert client.get(reverse("ops-orders")).json()["orders"] == []
