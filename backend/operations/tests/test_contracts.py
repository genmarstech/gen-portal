"""
Services catalogue and statements of work.

The guarantee this file defends: A CONTRACT DOES NOT CHANGE WHEN THE ORDER
CHANGES. Everything else here is bookkeeping around that one property.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.urls import reverse
from django.utils import timezone

from accounts.models import Membership, Organisation, User
from operations import services
from portal.models import Contract, Enquiry, Milestone, Order, Service

pytestmark = pytest.mark.django_db

PASSWORD = "correct-horse-battery"


@pytest.fixture
def staff() -> User:
    return User.objects.create_user(
        email="ops@genmars.co.ke", password=PASSWORD, full_name="Ops",
        is_staff=True, email_verified_at=timezone.now(),
    )


@pytest.fixture
def service() -> Service:
    return services.upsert_service(
        name="Payments and reconciliation",
        summary="Mobile money integration and automated reconciliation.",
        default_scope="Import statements, match against invoices, flag exceptions.",
        default_exclusions="No accounting-package integration. No historical migration.",
        default_deliverables="Import pipeline\nException review screen\nRunbook",
    )


@pytest.fixture
def client_user() -> User:
    return User.objects.create_user(
        email="client@example.com", password=PASSWORD, full_name="A Client",
        email_verified_at=timezone.now(),
    )


@pytest.fixture
def order(staff, client_user) -> Order:
    org = Organisation.objects.create(name="Kilimani Dental")
    Membership.objects.create(user=client_user, organisation=org)
    enquiry = Enquiry.objects.create(
        organisation=org, submitted_by=client_user, problem="Reconciling by hand."
    )
    o = services.convert_enquiry(
        enquiry=enquiry, actor=staff, title="Reconciliation tool",
        scope="Match M-Pesa statements to the appointment book.",
        exclusions="No dental-records integration.",
    )
    Milestone.objects.create(order=o, name="Deposit", amount_kes="150000.00", position=1)
    Milestone.objects.create(order=o, name="On acceptance", amount_kes="200000.00", position=2)
    return o


@pytest.fixture
def signed_in(client, staff):
    client.force_login(staff)
    return client


# ── the services catalogue ───────────────────────────────────────────────────


def test_a_service_prefills_scope_and_exclusions_on_conversion(staff, service, client_user):
    org = Organisation.objects.create(name="Rift Valley")
    enquiry = Enquiry.objects.create(
        organisation=org, submitted_by=client_user, problem="Manual entry."
    )
    order = services.convert_enquiry(
        enquiry=enquiry, actor=staff, title="Reconciliation", scope="", service=service
    )
    assert order.scope == service.default_scope
    assert order.exclusions == service.default_exclusions
    assert order.service == service


def test_editing_a_service_does_not_touch_orders_already_created(order, service, staff):
    """
    A service is a STARTING POINT. Improving its exclusions must not
    retroactively change what a client was told.
    """
    order.service = service
    order.save(update_fields=["service"])
    original = order.exclusions

    services.upsert_service(service=service, name=service.name,
                            default_exclusions="Completely different exclusions.")
    order.refresh_from_db()
    assert order.exclusions == original


def test_duplicate_service_names_are_refused(service):
    with pytest.raises(services.OperationsError):
        services.upsert_service(name="payments and reconciliation")


# ── the snapshot ─────────────────────────────────────────────────────────────


def test_issuing_freezes_the_wording_and_the_money(order, staff):
    """
    THE test in this file.

    A "contract" that renders the live order is a web page. Edit the order
    afterwards and the document the client signed now says something they never
    agreed to.
    """
    contract = services.issue_contract(order=order, actor=staff)
    assert contract.total_kes == 350000

    order.scope = "Something completely different that nobody agreed to."
    order.exclusions = "No exclusions at all."
    order.title = "Renamed"
    order.save()
    Milestone.objects.create(order=order, name="Sneaky extra", amount_kes="999.00", position=3)

    contract.refresh_from_db()
    assert contract.scope == "Match M-Pesa statements to the appointment book."
    assert contract.exclusions == "No dental-records integration."
    assert contract.title == "Reconciliation tool"
    assert contract.total_kes == 350000
    assert "Sneaky extra" not in contract.payment_terms


def test_the_total_is_summed_from_milestones_as_they_stood(order, staff):
    contract = services.issue_contract(order=order, actor=staff)
    assert "Deposit: KES 150000.00" in contract.payment_terms
    assert "On acceptance: KES 200000.00" in contract.payment_terms


def test_an_order_with_no_scope_cannot_be_contracted(order, staff):
    order.scope = "   "
    order.save(update_fields=["scope"])
    with pytest.raises(services.OperationsError):
        services.issue_contract(order=order, actor=staff)


def test_issuing_again_supersedes_and_keeps_both(order, staff):
    """
    What was agreed in March stays readable in September. That is the only
    reason anyone keeps contracts.
    """
    first = services.issue_contract(order=order, actor=staff)
    order.scope = "Revised scope, agreed at the review."
    order.save(update_fields=["scope"])
    second = services.issue_contract(order=order, actor=staff)

    first.refresh_from_db()
    assert first.status == Contract.Status.SUPERSEDED
    assert first.scope == "Match M-Pesa statements to the appointment book."
    assert second.version == 2
    assert second.scope == "Revised scope, agreed at the review."
    assert Contract.objects.filter(order=order).count() == 2


# ── signature ────────────────────────────────────────────────────────────────


def test_recording_a_signature_names_who_signed_and_who_wrote_it_down(order, staff):
    """
    This is a RECORD of a signature made elsewhere, not an e-signature. The
    fact the row establishes is that a named member of staff asserted it.
    """
    contract = services.issue_contract(order=order, actor=staff)
    signed = services.record_signature(
        contract=contract, actor=staff, signed_on=dt.date(2026, 9, 2),
        signed_by_name="Wanjiru Kamau", note="Countersigned PDF in 03-clients/.",
    )
    assert signed.status == Contract.Status.SIGNED
    assert signed.signed_by_name == "Wanjiru Kamau"
    assert signed.recorded_by == staff


def test_a_signature_needs_a_name_and_a_date(order, staff):
    contract = services.issue_contract(order=order, actor=staff)
    with pytest.raises(services.OperationsError):
        services.record_signature(
            contract=contract, actor=staff, signed_on=dt.date.today(), signed_by_name="  "
        )
    with pytest.raises(services.OperationsError):
        services.record_signature(
            contract=contract, actor=staff, signed_on=None, signed_by_name="Someone"
        )


def test_a_superseded_contract_cannot_be_signed(order, staff):
    first = services.issue_contract(order=order, actor=staff)
    services.issue_contract(order=order, actor=staff)
    first.refresh_from_db()
    with pytest.raises(services.OperationsError):
        services.record_signature(
            contract=first, actor=staff, signed_on=dt.date.today(), signed_by_name="X"
        )


def test_a_signed_contract_cannot_be_voided(order, staff):
    """Ending a signed agreement is done by agreement and a superseding
    version, not by deleting the evidence."""
    contract = services.issue_contract(order=order, actor=staff)
    services.record_signature(
        contract=contract, actor=staff, signed_on=dt.date.today(), signed_by_name="X"
    )
    with pytest.raises(services.OperationsError):
        services.void_contract(contract=contract, reason="Changed our mind.")


def test_voiding_needs_a_reason_and_keeps_the_row(order, staff):
    contract = services.issue_contract(order=order, actor=staff)
    with pytest.raises(services.OperationsError):
        services.void_contract(contract=contract, reason="  ")
    services.void_contract(contract=contract, reason="Issued against the wrong order.")
    contract.refresh_from_db()
    assert contract.status == Contract.Status.VOID
    assert Contract.objects.filter(pk=contract.pk).exists()


# ── what the client sees ─────────────────────────────────────────────────────


def test_the_client_sees_the_live_contract_and_not_a_draft(client, order, staff, client_user):
    from portal.selectors import live_contract_for

    assert live_contract_for(order) is None

    contract = services.issue_contract(order=order, actor=staff)
    assert live_contract_for(order) == contract

    client.force_login(client_user)
    body = client.get(reverse("order-detail", args=[order.reference])).json()
    assert body["contract"]["reference"] == contract.reference
    assert body["contract"]["total_kes"] == "350000.00"


def test_the_client_never_sees_a_superseded_or_void_version(client, order, staff, client_user):
    """Showing three versions invites arguing from the wrong one."""
    services.issue_contract(order=order, actor=staff)
    second = services.issue_contract(order=order, actor=staff)

    client.force_login(client_user)
    body = client.get(reverse("order-detail", args=[order.reference])).json()
    assert body["contract"]["version"] == second.version

    services.void_contract(contract=second, reason="Wrong figures.")
    body = client.get(reverse("order-detail", args=[order.reference])).json()
    assert body["contract"] is None


def test_the_client_is_not_shown_our_internal_signature_bookkeeping(client, order, staff, client_user):
    contract = services.issue_contract(order=order, actor=staff)
    services.record_signature(
        contract=contract, actor=staff, signed_on=dt.date.today(),
        signed_by_name="Wanjiru Kamau", note="Filed at 03-clients/kilimani/sow-v1.pdf",
    )
    client.force_login(client_user)
    payload = client.get(reverse("order-detail", args=[order.reference])).json()["contract"]

    assert "signature_note" not in payload
    assert "recorded_by" not in payload
    assert "03-clients" not in str(payload)
    # But they do see that they signed, and when.
    assert payload["signed_by_name"] == "Wanjiru Kamau"


def test_the_contract_is_in_the_data_export(order, staff, client_user):
    """Charter 05 §VIII — the agreement is the thing a client most needs a copy
    of, and we hold nothing hostage."""
    from portal.selectors import export_payload

    services.issue_contract(order=order, actor=staff)
    payload = export_payload(client_user)
    assert payload["orders"][0]["contract"]["total_kes"] == "350000.00"


def test_no_client_account_reaches_the_contract_endpoints(client, order, client_user):
    client.force_login(client_user)
    for url in [
        reverse("ops-services"),
        reverse("ops-contracts", args=[order.reference]),
        reverse("ops-contract-sign", args=[order.reference, 1]),
    ]:
        assert client.post(url, content_type="application/json").status_code == 403, url
