"""
Client CRUD: renaming, archiving, restoring and the narrow case for deleting.

═══════════════════════════════════════════════════════════════════════════════
THE TEST THAT MATTERS IS test_renaming_does_not_rewrite_invoices_already_issued.

Before Invoice.billed_to_name existed, a document read its billed-to line from
the live organisation. Renaming a client therefore changed the "To:" line on
every invoice already issued, sent and paid — so our copy of a numbered
document and the client's copy would disagree about who was billed, which is
the single thing an invoice number exists to make impossible.
═══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from accounts.models import Membership, Organisation, User
from operations import selectors, services
from portal.models import ActivityLog, ContactLogEntry, Contract, Invoice, Order

pytestmark = pytest.mark.django_db

PASSWORD = "correct-horse-battery"


def _staff(email: str, role: str) -> User:
    return User.objects.create_user(
        email=email, password=PASSWORD, full_name=email.split("@")[0],
        is_staff=True, staff_role=role, email_verified_at=timezone.now(),
    )


@pytest.fixture
def founder() -> User:
    return _staff("founder@genmars.co.ke", User.StaffRole.FOUNDER)


@pytest.fixture
def spa() -> Organisation:
    return Organisation.objects.create(name="Clips Serenity Spa")


def _billable_order(spa: Organisation, founder: User, reference="GM-2026-0001") -> Order:
    """
    An order with a SIGNED statement of work behind it.

    Charter 02 §I — the agreement comes before the bill, and issue_invoice
    enforces that. So anything here that needs an invoice needs this first.
    """
    order = Order.objects.create(
        organisation=spa, reference=reference, title="Site", contact=founder,
        scope="Build it.",
    )
    Contract.objects.create(
        order=order, version=1, title="Statement of work",
        scope="Build it.", total_kes=Decimal("100000.00"),
        status=Contract.Status.SIGNED, issued_by=founder,
        signed_on=timezone.localdate(), signed_by_name="The owner",
    )
    return order


# ── create ───────────────────────────────────────────────────────────────────


def test_a_client_is_created_and_logged(client, founder):
    client.force_login(founder)
    response = client.post(
        reverse("ops-organisations"), {"name": "Kilimani Dental"},
        content_type="application/json",
    )
    assert response.status_code == 201
    assert ActivityLog.objects.filter(action=ActivityLog.Action.CLIENT_CREATED).exists()


def test_a_duplicate_name_is_refused(client, founder, spa):
    client.force_login(founder)
    response = client.post(
        reverse("ops-organisations"), {"name": "clips serenity spa"},
        content_type="application/json",
    )
    assert response.status_code == 400
    assert response.json()["field"] == "name"


# ── rename ───────────────────────────────────────────────────────────────────


def test_renaming_does_not_rewrite_invoices_already_issued(client, founder, spa):
    """
    ═══════════════════════════════════════════════════════════════════════════
    The invoice keeps the name it was issued under. Forever.
    ═══════════════════════════════════════════════════════════════════════════
    """
    order = _billable_order(spa, founder)
    invoice = services.issue_invoice(
        order=order, actor=founder, description="Deposit", amount_kes=Decimal("50000.00")
    )

    client.force_login(founder)
    assert client.patch(
        reverse("ops-client-admin", args=[spa.pk]),
        {"name": "Serenity Spa & Wellness"},
        content_type="application/json",
    ).status_code == 200

    spa.refresh_from_db()
    assert spa.name == "Serenity Spa & Wellness"
    assert Invoice.objects.get(pk=invoice.pk).billed_to_name == "Clips Serenity Spa"

    # And the document the CLIENT opens still says who it was billed to on the
    # day — which is the copy that has to match the one already in their inbox.
    owner = User.objects.create_user(
        email="owner@spa.co.ke", password=PASSWORD, email_verified_at=timezone.now()
    )
    Membership.objects.create(user=owner, organisation=spa)
    client.logout()
    client.force_login(owner)

    document = client.get(reverse("invoice-document-flat", args=[invoice.number])).json()
    assert document["billed_to"]["organisation"] == "Clips Serenity Spa"


def test_an_invoice_issued_after_a_rename_uses_the_new_name(client, founder, spa):
    client.force_login(founder)
    client.patch(
        reverse("ops-client-admin", args=[spa.pk]),
        {"name": "Serenity Spa & Wellness"},
        content_type="application/json",
    )
    spa.refresh_from_db()

    order = _billable_order(spa, founder, reference="GM-2026-0002")
    invoice = services.issue_invoice(
        order=order, actor=founder, description="Deposit", amount_kes=Decimal("1000.00")
    )
    assert invoice.billed_to_name == "Serenity Spa & Wellness"


def test_renaming_to_an_existing_name_is_refused(client, founder, spa):
    Organisation.objects.create(name="Kilimani Dental")
    client.force_login(founder)
    response = client.patch(
        reverse("ops-client-admin", args=[spa.pk]),
        {"name": "kilimani dental"},
        content_type="application/json",
    )
    assert response.status_code == 400
    assert response.json()["field"] == "name"


def test_a_rename_keeps_the_old_name_in_the_log(client, founder, spa):
    """Otherwise nothing anywhere connects an old invoice to the client it
    belongs to."""
    client.force_login(founder)
    client.patch(
        reverse("ops-client-admin", args=[spa.pk]),
        {"name": "Serenity Spa & Wellness"},
        content_type="application/json",
    )
    entry = ActivityLog.objects.get(action=ActivityLog.Action.CLIENT_RENAMED)
    assert entry.detail["was"] == "Clips Serenity Spa"


# ── archive ──────────────────────────────────────────────────────────────────


def test_archiving_hides_a_client_without_deleting_anything(client, founder, spa):
    ContactLogEntry.objects.create(
        organisation=spa, channel="call", direction="inbound", summary="A call",
    )
    client.force_login(founder)
    assert client.post(
        reverse("ops-client-admin", args=[spa.pk]),
        {"action": "archive", "reason": "They sold the business"},
        content_type="application/json",
    ).status_code == 200

    spa.refresh_from_db()
    assert spa.is_archived
    # Gone from the working list...
    assert not selectors.organisations().filter(pk=spa.pk).exists()
    # ...but the record is whole.
    assert selectors.organisations(include_archived=True).filter(pk=spa.pk).exists()
    assert ContactLogEntry.objects.filter(organisation=spa).count() == 1


def test_an_archived_client_is_still_readable(client, founder, spa):
    """
    A link from an old invoice to a 404 reads as the record having been
    destroyed, which is the impression archiving exists to avoid.
    """
    client.force_login(founder)
    client.post(
        reverse("ops-client-admin", args=[spa.pk]),
        {"action": "archive"}, content_type="application/json",
    )
    assert client.get(reverse("ops-client", args=[spa.pk])).status_code == 200


def test_a_client_with_an_unpaid_invoice_cannot_be_archived(client, founder, spa):
    """
    A hidden client with money outstanding is money nobody chases. "We stopped
    working with them" and "they never paid the last invoice" are the same
    conversation more often than not.
    """
    order = _billable_order(spa, founder)
    invoice = services.issue_invoice(
        order=order, actor=founder, description="Final", amount_kes=Decimal("80000.00")
    )

    client.force_login(founder)
    response = client.post(
        reverse("ops-client-admin", args=[spa.pk]),
        {"action": "archive"}, content_type="application/json",
    )
    assert response.status_code == 400
    assert invoice.number in response.json()["detail"]

    spa.refresh_from_db()
    assert not spa.is_archived


def test_a_settled_invoice_does_not_block_archiving(client, founder, spa):
    order = _billable_order(spa, founder)
    invoice = services.issue_invoice(
        order=order, actor=founder, description="Final", amount_kes=Decimal("500.00")
    )
    services.record_payment(invoice=invoice, actor=founder, reference="ABC123")

    client.force_login(founder)
    assert client.post(
        reverse("ops-client-admin", args=[spa.pk]),
        {"action": "archive"}, content_type="application/json",
    ).status_code == 200


def test_archiving_twice_is_refused(client, founder, spa):
    client.force_login(founder)
    url = reverse("ops-client-admin", args=[spa.pk])
    client.post(url, {"action": "archive"}, content_type="application/json")
    assert client.post(
        url, {"action": "archive"}, content_type="application/json"
    ).status_code == 400


def test_a_client_can_be_restored(client, founder, spa):
    client.force_login(founder)
    url = reverse("ops-client-admin", args=[spa.pk])
    client.post(url, {"action": "archive"}, content_type="application/json")
    assert client.post(
        url, {"action": "restore"}, content_type="application/json"
    ).status_code == 200

    spa.refresh_from_db()
    assert not spa.is_archived
    assert selectors.organisations().filter(pk=spa.pk).exists()


def test_the_archived_list_is_behind_a_flag(client, founder, spa):
    client.force_login(founder)
    client.post(
        reverse("ops-client-admin", args=[spa.pk]),
        {"action": "archive"}, content_type="application/json",
    )

    working = client.get(reverse("ops-organisations")).json()
    assert working["organisations"] == []
    assert working["archived_count"] == 1

    everything = client.get(reverse("ops-organisations"), {"archived": "1"}).json()
    assert [o["name"] for o in everything["organisations"]] == ["Clips Serenity Spa"]


# ── delete ───────────────────────────────────────────────────────────────────


def test_a_client_with_nothing_attached_can_be_deleted(client, founder):
    """The honest case: a name typed twice, five minutes ago."""
    duplicate = Organisation.objects.create(name="Kilimani Dentl")
    client.force_login(founder)

    assert client.delete(reverse("ops-client-admin", args=[duplicate.pk])).status_code == 204
    assert not Organisation.objects.filter(pk=duplicate.pk).exists()
    assert ActivityLog.objects.filter(action=ActivityLog.Action.CLIENT_DELETED).exists()


@pytest.mark.parametrize("attach", ["invoice", "conversation", "member"])
def test_deleting_refuses_the_moment_anything_is_attached(client, founder, spa, attach):
    """
    Organisation cascades. A delete that went through would take invoices that
    were issued, sent and paid — accounting records, not ours to remove because
    a relationship ended.
    """
    if attach == "invoice":
        order = _billable_order(spa, founder)
        services.issue_invoice(
            order=order, actor=founder, description="Deposit", amount_kes=Decimal("100.00")
        )
    elif attach == "conversation":
        ContactLogEntry.objects.create(
            organisation=spa, channel="call", direction="inbound", summary="A call"
        )
    else:
        member = User.objects.create_user(email="owner@spa.co.ke", password=PASSWORD)
        Membership.objects.create(user=member, organisation=spa)

    client.force_login(founder)
    response = client.delete(reverse("ops-client-admin", args=[spa.pk]))

    assert response.status_code == 400
    assert "Archive them instead" in response.json()["detail"]
    assert Organisation.objects.filter(pk=spa.pk).exists()


def test_every_blocking_relation_is_a_real_one():
    """
    A typo in ATTACHMENTS_BLOCKING_DELETE would silently stop blocking on that
    relation, and the first anyone knew would be a cascade that took an invoice
    with it.
    """
    real = {f.get_accessor_name() for f in Organisation._meta.related_objects}
    for relation, _ in services.ATTACHMENTS_BLOCKING_DELETE:
        assert relation in real, relation


def test_the_capabilities_the_screen_hides_controls_with(client, founder):
    """
    For hiding controls only — every write is checked again on the server. It
    exists so the UI does not offer a button that is going to 403.
    """
    client.force_login(founder)
    may = client.get(reverse("ops-organisations")).json()["may"]
    assert may == {
        "add": True, "rename": True, "manage_access": True,
        "archive": True, "delete": True,
    }

    engineer = _staff("dev@genmars.co.ke", User.StaffRole.DELIVERY)
    client.force_login(engineer)
    may = client.get(reverse("ops-organisations")).json()["may"]
    assert may["add"] is False
    assert may["archive"] is False
    assert may["rename"] is True
