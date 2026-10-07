"""
Removing an order, and the two things that must survive the attempt.

═══════════════════════════════════════════════════════════════════════════════
THE ONE THAT MATTERS: test_a_signed_contract_stops_the_delete.

`Invoice.order` is PROTECT, so the database itself refuses to let a billed
order go. `Contract.order` is CASCADE, and it does not — a signed contract is
the snapshot of what a client agreed to under Charter 05 §I, and deleting the
order would take it with it, silently, leaving nothing to say the agreement
ever existed.

The database will not stop that. `services.delete_order` does, and this is the
test that says so.
═══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from accounts.models import Organisation, User
from operations import services
from portal.models import (
    ActivityLog,
    Blocker,
    Contract,
    Invoice,
    Order,
    ProgressNote,
)

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
def founder() -> User:
    return _user("founder@genmars.co.ke", User.StaffRole.FOUNDER)


@pytest.fixture
def delivery() -> User:
    return _user("delivery@genmars.co.ke", User.StaffRole.DELIVERY)


@pytest.fixture
def org() -> Organisation:
    return Organisation.objects.create(name="Riverside Dental")


@pytest.fixture
def contact(org) -> User:
    user = User.objects.create_user(
        email="client@riverside.example",
        password=PASSWORD,
        full_name="A Client",
        email_verified_at=timezone.now(),
    )
    return user


@pytest.fixture
def order(org, contact) -> Order:
    return Order.objects.create(
        organisation=org,
        reference="ORD-2026-099",
        title="A test order",
        scope="Something to delete.",
        contact=contact,
    )


def _sign_in(client, user: User) -> None:
    assert client.login(email=user.email, password=PASSWORD)


# ── the preview ─────────────────────────────────────────────────────────────


def test_the_preview_counts_what_would_go_with_it(order, founder):
    ProgressNote.objects.create(order=order, body="One", week_of=date(2026, 10, 5), author=founder)
    ProgressNote.objects.create(order=order, body="Two", week_of=date(2026, 9, 28), author=founder)
    Blocker.objects.create(order=order, summary="Waiting on access", raised_by=founder)

    preview = services.order_deletion_preview(order)

    assert preview["may_delete"] is True
    labelled = {row["label"]: row["count"] for row in preview["cascades"]}
    assert labelled["progress notes"] == 2
    assert labelled["blocker"] == 1


def test_an_order_with_nothing_attached_says_so(order):
    preview = services.order_deletion_preview(order)
    assert preview["may_delete"] is True
    assert preview["cascades"] == []


def test_the_preview_is_readable_by_any_member_of_staff(client, order, delivery):
    """
    Deliberately wider than the delete. "What is attached to this?" is a
    question worth answering without intending to act on it.
    """
    _sign_in(client, delivery)
    response = client.get(reverse("ops-order-deletion", args=[order.reference]))
    assert response.status_code == 200
    assert response.json()["reference"] == order.reference


# ── the two refusals ────────────────────────────────────────────────────────


def test_an_invoiced_order_cannot_be_deleted(order, founder, org):
    Invoice.objects.create(
        number="INV-2026-001",
        organisation=org,
        order=order,
        description="Phase one",
        amount_kes=Decimal("50000"),
        issued_on=date(2026, 10, 1),
    )

    preview = services.order_deletion_preview(order)
    assert preview["may_delete"] is False
    assert [b["kind"] for b in preview["blockers"]] == ["invoices"]

    with pytest.raises(services.OperationsError):
        services.delete_order(actor=founder, order=order)

    assert Order.objects.filter(pk=order.pk).exists()


def test_a_signed_contract_stops_the_delete(order, founder):
    """
    The one the database would have allowed. See this file's banner.
    """
    contract = Contract.objects.create(
        order=order,
        title="Statement of work",
        scope="The agreed thing.",
        status=Contract.Status.SIGNED,
    )

    preview = services.order_deletion_preview(order)
    assert preview["may_delete"] is False
    assert [b["kind"] for b in preview["blockers"]] == ["signed_contracts"]

    with pytest.raises(services.OperationsError):
        services.delete_order(actor=founder, order=order)

    assert Order.objects.filter(pk=order.pk).exists()
    assert Contract.objects.filter(pk=contract.pk).exists()


def test_an_unsigned_contract_does_not_stop_it_but_is_reported(order, founder):
    """
    A draft is a document nobody has agreed to. It goes, and the preview says
    it is going — which is the difference between a cascade and a surprise.
    """
    Contract.objects.create(
        order=order,
        title="Draft",
        scope="Not agreed.",
        status=Contract.Status.DRAFT,
    )

    preview = services.order_deletion_preview(order)
    assert preview["may_delete"] is True
    assert {"label": "unsigned contract", "count": 1} in preview["cascades"]


# ── the delete ──────────────────────────────────────────────────────────────


def test_deleting_takes_the_delivery_record_with_it(order, founder):
    ProgressNote.objects.create(order=order, body="One", week_of=date(2026, 10, 5), author=founder)
    Blocker.objects.create(order=order, summary="Waiting", raised_by=founder)

    services.delete_order(actor=founder, order=order)

    assert not Order.objects.filter(reference="ORD-2026-099").exists()
    assert ProgressNote.objects.count() == 0
    assert Blocker.objects.count() == 0


def test_the_log_line_carries_what_went_with_it(order, founder):
    """
    Written BEFORE the delete, because afterwards there is nothing left to
    count — and the counts are the whole value of the entry months later.
    """
    ProgressNote.objects.create(order=order, body="One", week_of=date(2026, 10, 5), author=founder)
    ProgressNote.objects.create(order=order, body="Two", week_of=date(2026, 9, 28), author=founder)

    services.delete_order(actor=founder, order=order)

    entry = ActivityLog.objects.get(action=ActivityLog.Action.ORDER_DELETED)
    assert entry.subject == "ORD-2026-099"
    assert "2 progress notes" in entry.summary
    assert entry.detail["title"] == "A test order"


def test_an_order_with_nothing_attached_logs_that_plainly(order, founder):
    services.delete_order(actor=founder, order=order)
    entry = ActivityLog.objects.get(action=ActivityLog.Action.ORDER_DELETED)
    assert "nothing attached" in entry.summary


# ── who may ─────────────────────────────────────────────────────────────────


def test_only_a_founder_may_delete(client, order, delivery):
    _sign_in(client, delivery)
    response = client.delete(reverse("ops-order", args=[order.reference]))
    assert response.status_code == 403
    assert Order.objects.filter(pk=order.pk).exists()


def test_a_founder_may(client, order, founder):
    _sign_in(client, founder)
    response = client.delete(reverse("ops-order", args=[order.reference]))
    assert response.status_code == 200
    assert response.json()["deleted"]["reference"] == "ORD-2026-099"
    assert not Order.objects.filter(pk=order.pk).exists()


def test_a_refusal_is_409_and_says_why(client, order, founder, org):
    """
    Not 400: the request is well formed and the caller is allowed to make it.
    What refuses is the state of the order.
    """
    Invoice.objects.create(
        number="INV-2026-002",
        organisation=org,
        order=order,
        description="Phase one",
        amount_kes=Decimal("50000"),
        issued_on=date(2026, 10, 1),
    )
    _sign_in(client, founder)
    response = client.delete(reverse("ops-order", args=[order.reference]))
    assert response.status_code == 409
    assert "invoice" in response.json()["detail"].lower()


def test_deleting_something_that_is_not_there_is_404(client, founder):
    _sign_in(client, founder)
    response = client.delete(reverse("ops-order", args=["ORD-NOPE"]))
    assert response.status_code == 404
