"""
Invoicing.

An invoice reaches a client's accounts department, gets paid, and lands in two
sets of books. Getting it wrong is not a UI bug — it is a conversation about
money with someone who trusted us. So the tests here are mostly about what the
system REFUSES.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from accounts.models import Membership, Organisation, User
from operations import services
from portal.models import Contract, Enquiry, Invoice, Milestone, Order

pytestmark = pytest.mark.django_db

PASSWORD = "correct-horse-battery"


@pytest.fixture
def staff() -> User:
    return User.objects.create_user(
        email="ops@genmars.co.ke", password=PASSWORD, full_name="Ops",
        # Billing is the same authority as pricing and signing (Charter 02 §I).
        is_staff=True, staff_role=User.StaffRole.COMMERCIAL,
        email_verified_at=timezone.now(),
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
    order = services.convert_enquiry(
        enquiry=enquiry, actor=staff, title="Reconciliation tool", scope="Do the thing."
    )
    Milestone.objects.create(
        order=order, name="On signature", amount_kes=Decimal("150000.00"), position=1
    )
    Milestone.objects.create(
        order=order, name="On delivery", amount_kes=Decimal("250000.00"), position=2
    )
    return order


@pytest.fixture
def signed(order, staff) -> Order:
    """An order with a signed SOW — the precondition for billing anything."""
    contract = services.issue_contract(order=order, actor=staff)
    services.record_signature(
        contract=contract, actor=staff, signed_on=timezone.localdate(),
        signed_by_name="Dr Wanjiku", note="Countersigned PDF in Drive.",
    )
    return order


# ── you cannot bill for something nobody agreed to ───────────────────────────


def test_an_order_with_no_signed_contract_cannot_be_invoiced(order, staff):
    """
    Charter 02 §I — the agreement comes before the work, and before the bill.
    This is the guard most likely to be resented in a hurry and the one most
    worth keeping.
    """
    with pytest.raises(services.OperationsError) as caught:
        services.issue_invoice(
            order=order, actor=staff, milestone=order.milestones.first()
        )
    assert "signed statement of work" in str(caught.value)
    assert Invoice.objects.count() == 0


def test_an_issued_but_unsigned_contract_is_not_enough(order, staff):
    """Issued means we sent it. Signed means they agreed."""
    services.issue_contract(order=order, actor=staff)
    with pytest.raises(services.OperationsError):
        services.issue_invoice(order=order, actor=staff, milestone=order.milestones.first())


# ── the snapshot ─────────────────────────────────────────────────────────────


def test_issuing_copies_the_amount_rather_than_pointing_at_it(signed, staff):
    """
    An invoice that recalculates is not an invoice. Change the milestone
    afterwards and the document already in the client's hands must not move.
    """
    milestone = signed.milestones.first()
    invoice = services.issue_invoice(order=signed, actor=staff, milestone=milestone)
    assert invoice.amount_kes == Decimal("150000.00")
    assert invoice.description == "On signature"

    milestone.amount_kes = Decimal("999999.00")
    milestone.name = "Renamed entirely"
    milestone.save()

    invoice.refresh_from_db()
    assert invoice.amount_kes == Decimal("150000.00")
    assert invoice.description == "On signature"


def test_billing_a_milestone_marks_it_invoiced(signed, staff):
    milestone = signed.milestones.first()
    services.issue_invoice(order=signed, actor=staff, milestone=milestone)
    milestone.refresh_from_db()
    assert milestone.status == Milestone.Status.INVOICED


# ── double billing ───────────────────────────────────────────────────────────


def test_a_milestone_cannot_be_billed_twice(signed, staff):
    """
    The second invoice looks exactly like a legitimate one to whoever receives
    it, which is what makes this worth refusing rather than warning about.
    """
    milestone = signed.milestones.first()
    first = services.issue_invoice(order=signed, actor=staff, milestone=milestone)

    with pytest.raises(services.OperationsError) as caught:
        services.issue_invoice(order=signed, actor=staff, milestone=milestone)

    # It names the invoice, so the person can go and look at it.
    assert first.number in str(caught.value)
    assert Invoice.objects.count() == 1


def test_voiding_makes_the_milestone_billable_again(signed, staff):
    """That is the entire point of a void — it is how a mistake is corrected."""
    milestone = signed.milestones.first()
    wrong = services.issue_invoice(order=signed, actor=staff, milestone=milestone)
    services.void_invoice(invoice=wrong, actor=staff, reason="Wrong amount.")

    milestone.refresh_from_db()
    assert milestone.status == Milestone.Status.PENDING

    right = services.issue_invoice(
        order=signed, actor=staff, milestone=milestone, amount_kes=Decimal("175000.00")
    )
    assert right.amount_kes == Decimal("175000.00")
    # And the wrong one keeps its number rather than being reused.
    assert right.number != wrong.number


def test_a_milestone_from_another_order_is_refused(signed, staff, client_user):
    other = services.convert_enquiry(
        enquiry=Enquiry.objects.create(
            organisation=Organisation.objects.create(name="Somebody Else"),
            submitted_by=client_user, problem="Different problem entirely.",
        ),
        actor=staff, title="Other work", scope="Other scope.",
    )
    stray = Milestone.objects.create(
        order=other, name="Theirs", amount_kes=Decimal("100.00")
    )
    with pytest.raises(services.OperationsError):
        services.issue_invoice(order=signed, actor=staff, milestone=stray)


# ── amounts ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("amount", [Decimal("0.00"), Decimal("-5000.00")])
def test_an_invoice_must_ask_for_a_positive_amount(signed, staff, amount):
    """A negative invoice is a credit note — a different document with
    different accounting treatment, not something to smuggle through here."""
    with pytest.raises(services.OperationsError):
        services.issue_invoice(
            order=signed, actor=staff, description="Something", amount_kes=amount
        )


def test_money_stays_a_decimal_end_to_end(signed, staff):
    """
    Money through a float is money you cannot reconcile against a statement.
    0.1 + 0.2 as floats is not 0.3, and an invoice is exactly where that shows.
    """
    invoice = services.issue_invoice(
        order=signed, actor=staff, description="Odd amount",
        amount_kes=Decimal("133333.33"),
    )
    invoice.refresh_from_db()
    assert isinstance(invoice.amount_kes, Decimal)
    assert invoice.amount_kes == Decimal("133333.33")


def test_a_due_date_before_the_invoice_date_is_refused(signed, staff):
    """It would arrive already overdue."""
    today = timezone.localdate()
    with pytest.raises(services.OperationsError):
        services.issue_invoice(
            order=signed, actor=staff, description="Work",
            amount_kes=Decimal("1000.00"),
            issued_on=today, due_on=today - timedelta(days=1),
        )


# ── numbering ────────────────────────────────────────────────────────────────


def test_numbers_are_sequential_and_a_void_does_not_free_one(signed, staff):
    """
    A reused number puts two different documents in the world under one
    reference, which is the single thing an invoice number exists to prevent.
    Gaps are explainable; reuse is not.
    """
    first = services.issue_invoice(
        order=signed, actor=staff, description="One", amount_kes=Decimal("100.00")
    )
    second = services.issue_invoice(
        order=signed, actor=staff, description="Two", amount_kes=Decimal("200.00")
    )
    services.void_invoice(invoice=second, actor=staff, reason="Sent in error.")
    third = services.issue_invoice(
        order=signed, actor=staff, description="Three", amount_kes=Decimal("300.00")
    )

    year = timezone.localdate().year
    assert first.number == f"GM-INV-{year}-0001"
    assert second.number == f"GM-INV-{year}-0002"
    # NOT 0002 again, even though 0002 is void and owed by nobody.
    assert third.number == f"GM-INV-{year}-0003"


# ── payment is recorded, not collected ───────────────────────────────────────


def test_recording_a_payment_needs_a_reference(signed, staff):
    """Without it the row cannot be checked against the bank account, which is
    the only thing that makes it evidence rather than an assertion."""
    invoice = services.issue_invoice(
        order=signed, actor=staff, milestone=signed.milestones.first()
    )
    with pytest.raises(services.OperationsError):
        services.record_payment(invoice=invoice, actor=staff, reference="   ")


def test_recording_a_payment_marks_the_milestone_paid(signed, staff):
    milestone = signed.milestones.first()
    invoice = services.issue_invoice(order=signed, actor=staff, milestone=milestone)
    services.record_payment(invoice=invoice, actor=staff, reference="SLJ7XK2P1Q")

    invoice.refresh_from_db()
    milestone.refresh_from_db()
    assert invoice.status == Invoice.Status.PAID
    assert invoice.payment_reference == "SLJ7XK2P1Q"
    assert invoice.recorded_by == staff
    assert milestone.status == Milestone.Status.PAID


def test_a_payment_cannot_predate_the_invoice(signed, staff):
    invoice = services.issue_invoice(
        order=signed, actor=staff, milestone=signed.milestones.first()
    )
    with pytest.raises(services.OperationsError):
        services.record_payment(
            invoice=invoice, actor=staff, reference="X1",
            paid_on=invoice.issued_on - timedelta(days=1),
        )


def test_paying_twice_is_refused(signed, staff):
    invoice = services.issue_invoice(
        order=signed, actor=staff, milestone=signed.milestones.first()
    )
    services.record_payment(invoice=invoice, actor=staff, reference="FIRST")
    with pytest.raises(services.OperationsError):
        services.record_payment(invoice=invoice, actor=staff, reference="SECOND")
    invoice.refresh_from_db()
    assert invoice.payment_reference == "FIRST"


def test_a_voided_invoice_cannot_be_paid(signed, staff):
    invoice = services.issue_invoice(
        order=signed, actor=staff, description="Wrong", amount_kes=Decimal("100.00")
    )
    services.void_invoice(invoice=invoice, actor=staff, reason="Sent to the wrong client.")
    with pytest.raises(services.OperationsError):
        services.record_payment(invoice=invoice, actor=staff, reference="ANYTHING")


# ── voiding ──────────────────────────────────────────────────────────────────


def test_a_paid_invoice_cannot_be_voided(signed, staff):
    """
    THE MOST IMPORTANT REFUSAL HERE.

    Voiding a paid invoice erases the record of money that actually arrived,
    leaving the client's statement showing a payment against a document we say
    never existed. If money must go back that is a refund and a credit note —
    a real transaction, not the deletion of a row.
    """
    invoice = services.issue_invoice(
        order=signed, actor=staff, milestone=signed.milestones.first()
    )
    services.record_payment(invoice=invoice, actor=staff, reference="SLJ7XK2P1Q")

    with pytest.raises(services.OperationsError) as caught:
        services.void_invoice(invoice=invoice, actor=staff, reason="Changed our mind.")

    assert "credit note" in str(caught.value)
    invoice.refresh_from_db()
    assert invoice.status == Invoice.Status.PAID
    assert invoice.payment_reference == "SLJ7XK2P1Q"


def test_voiding_requires_a_reason(signed, staff):
    invoice = services.issue_invoice(
        order=signed, actor=staff, description="X", amount_kes=Decimal("100.00")
    )
    with pytest.raises(services.OperationsError):
        services.void_invoice(invoice=invoice, actor=staff, reason="  ")


# ── overdue is a claim about a moment ────────────────────────────────────────


def test_overdue_is_unpaid_and_past_due(signed, staff):
    today = timezone.localdate()
    invoice = services.issue_invoice(
        order=signed, actor=staff, description="Work", amount_kes=Decimal("100.00"),
        issued_on=today - timedelta(days=40), due_on=today - timedelta(days=10),
    )
    assert invoice.is_overdue(today) is True

    services.record_payment(invoice=invoice, actor=staff, reference="LATE-BUT-PAID")
    invoice.refresh_from_db()
    # Paid late is not overdue. It is paid.
    assert invoice.is_overdue(today) is False


def test_an_invoice_with_no_due_date_is_never_overdue(signed, staff):
    """We did not state a date, so we cannot claim they missed one."""
    invoice = services.issue_invoice(
        order=signed, actor=staff, description="Work", amount_kes=Decimal("100.00"),
        due_on=None,
    )
    assert invoice.is_overdue(timezone.localdate() + timedelta(days=365)) is False


# ── what the client sees ─────────────────────────────────────────────────────


def test_the_client_sees_their_invoices(client, signed, staff, client_user):
    invoice = services.issue_invoice(
        order=signed, actor=staff, milestone=signed.milestones.first()
    )
    client.force_login(client_user)
    body = client.get(reverse("order-detail", args=[signed.reference])).json()

    assert len(body["invoices"]) == 1
    shown = body["invoices"][0]
    assert shown["number"] == invoice.number
    # A STRING — this is a number somebody pays.
    assert shown["amount_kes"] == "150000.00"


def test_a_voided_invoice_is_shown_to_the_client_with_its_reason(
    client, signed, staff, client_user
):
    """
    We already SENT it. Hiding it means they hold a document the portal says
    does not exist, and they find out it was withdrawn by paying it.
    """
    invoice = services.issue_invoice(
        order=signed, actor=staff, description="Duplicate", amount_kes=Decimal("100.00")
    )
    services.void_invoice(
        invoice=invoice, actor=staff, reason="Duplicate of GM-INV-2026-0001."
    )
    client.force_login(client_user)
    body = client.get(reverse("order-detail", args=[signed.reference])).json()

    shown = body["invoices"][0]
    assert shown["status"] == "void"
    assert "Duplicate of" in shown["void_reason"]


def test_a_client_cannot_see_another_organisations_invoices(client, signed, staff):
    outsider = User.objects.create_user(
        email="outsider@example.com", password=PASSWORD,
        email_verified_at=timezone.now(),
    )
    services.issue_invoice(order=signed, actor=staff, milestone=signed.milestones.first())
    client.force_login(outsider)
    assert client.get(reverse("order-detail", args=[signed.reference])).status_code == 404


def test_billing_history_is_in_the_data_export(signed, staff, client_user):
    """Charter 05 §VIII — a client who leaves should not have to ask us for
    their own billing history."""
    from portal.selectors import export_payload

    invoice = services.issue_invoice(
        order=signed, actor=staff, milestone=signed.milestones.first()
    )
    services.record_payment(invoice=invoice, actor=staff, reference="SLJ7XK2P1Q")

    exported = export_payload(client_user)["orders"][0]["invoices"]
    assert exported[0]["number"] == invoice.number
    assert exported[0]["payment_reference"] == "SLJ7XK2P1Q"
    assert exported[0]["amount_kes"] == "150000.00"


# ── who may bill ─────────────────────────────────────────────────────────────


def test_an_engineer_cannot_issue_an_invoice(client, signed):
    """Charter 02 §I keeps money with the founder and the commercial partners."""
    engineer = User.objects.create_user(
        email="engineer@genmars.co.ke", password=PASSWORD, is_staff=True,
        staff_role=User.StaffRole.DELIVERY, email_verified_at=timezone.now(),
    )
    client.force_login(engineer)
    response = client.post(
        reverse("ops-invoices", args=[signed.reference]),
        {"description": "Work", "amount_kes": "100.00"},
        content_type="application/json",
    )
    assert response.status_code == 403
    assert Invoice.objects.count() == 0


def test_a_commercial_partner_can_bill_over_http(client, signed, staff):
    client.force_login(staff)
    response = client.post(
        reverse("ops-invoices", args=[signed.reference]),
        {"milestone": signed.milestones.first().pk, "due_on": "2026-12-31"},
        content_type="application/json",
    )
    assert response.status_code == 201, response.json()
    assert response.json()["amount_kes"] == "150000.00"


def test_an_ad_hoc_invoice_can_be_issued_without_naming_a_milestone(client, signed, staff):
    """
    A change request or a retainer month is genuinely not a milestone.

    This goes over HTTP on purpose. The service-level tests pass `milestone=None`
    explicitly and so never exercise the serializer with the key ABSENT — which
    is how the real request arrives, and which returned a KeyError 500 until
    InvoiceWriteSerializer was given `default=None`.
    """
    client.force_login(staff)
    response = client.post(
        reverse("ops-invoices", args=[signed.reference]),
        {"description": "Change request: export to QuickBooks", "amount_kes": "45000.00"},
        content_type="application/json",
    )
    assert response.status_code == 201, response.json()
    body = response.json()
    assert body["milestone"] is None
    assert body["amount_kes"] == "45000.00"


def test_a_negative_amount_is_refused_over_http(client, signed, staff):
    """The same path, reaching the guard rather than a 500."""
    client.force_login(staff)
    response = client.post(
        reverse("ops-invoices", args=[signed.reference]),
        {"description": "Refund", "amount_kes": "-5000.00"},
        content_type="application/json",
    )
    assert response.status_code == 400, response.json()
    assert "credit note" in response.json()["detail"]


# ── the invoice document ─────────────────────────────────────────────────────


def test_the_document_carries_everything_needed_to_pay_it(
    client, signed, staff, client_user
):
    invoice = services.issue_invoice(
        order=signed, actor=staff, milestone=signed.milestones.first(),
        due_on=timezone.localdate() + timedelta(days=30),
    )
    client.force_login(client_user)
    body = client.get(
        reverse("invoice-document", args=[signed.reference, invoice.number])
    ).json()

    assert body["invoice"]["number"] == invoice.number
    assert body["invoice"]["amount_kes"] == "150000.00"
    assert body["billed_to"]["organisation"] == "Kilimani Dental"
    assert body["biller"]["legal_name"] == "Genmars Tech Limited"
    # The agreement it arises from, so "what is this for" needs no phone call.
    assert body["order"]["contract_reference"] == f"{signed.reference}-SOW-01"
    assert body["order"]["contract_signed_on"] is not None


def test_unconfigured_billing_details_are_absent_rather_than_blank(
    client, signed, staff, client_user
):
    """
    Charter 04 §IV. An invoice carrying an invented KRA PIN or paybill is not a
    cosmetic bug — it is a document somebody pays against, or fails to file.
    Nothing is configured in tests, so nothing must be asserted.
    """
    invoice = services.issue_invoice(
        order=signed, actor=staff, description="Work", amount_kes=Decimal("1000.00")
    )
    client.force_login(client_user)
    body = client.get(
        reverse("invoice-document", args=[signed.reference, invoice.number])
    ).json()

    assert body["biller"]["kra_pin"] is None
    assert body["payment"]["mpesa_paybill"] is None
    assert body["payment"]["bank_details"] is None
    # And the page must not offer a payment it cannot take.
    assert body["payment"]["stk_available"] is False


def test_the_paybill_account_carries_the_invoice_number(
    client, signed, staff, client_user, settings
):
    """Putting the invoice number in the account field is what makes a payment
    reconcilable without a phone call."""
    settings.BILLING_MPESA_PAYBILL = "247247"
    settings.BILLING_MPESA_ACCOUNT_HINT = "GEN{number}"

    invoice = services.issue_invoice(
        order=signed, actor=staff, description="Work", amount_kes=Decimal("1000.00")
    )
    client.force_login(client_user)
    body = client.get(
        reverse("invoice-document", args=[signed.reference, invoice.number])
    ).json()

    assert body["payment"]["mpesa_paybill"] == "247247"
    assert body["payment"]["mpesa_account"] == f"GEN{invoice.number}"


def test_another_clients_invoice_is_404_not_403(client, signed, staff):
    """
    THE ENUMERATION ORACLE.

    Invoice numbers are sequential and therefore guessable. A 403 would confirm
    which numbers are real — telling an outsider how much business Genmars is
    doing, and with whom, one request at a time. It must be indistinguishable
    from a number that was never issued.
    """
    invoice = services.issue_invoice(
        order=signed, actor=staff, milestone=signed.milestones.first()
    )
    outsider = User.objects.create_user(
        email="outsider@example.com", password=PASSWORD,
        email_verified_at=timezone.now(),
    )
    client.force_login(outsider)

    real = client.get(
        reverse("invoice-document", args=[signed.reference, invoice.number])
    )
    invented = client.get(
        reverse("invoice-document", args=[signed.reference, "GM-INV-2026-9999"])
    )

    assert real.status_code == 404
    # Byte-identical, not merely both 404 — a difference in the body is the
    # same oracle wearing a different hat.
    assert real.content == invented.content


def test_an_invoice_number_from_another_order_is_not_reachable(
    client, signed, staff, client_user
):
    """The reference and the number must agree. Otherwise a client could read
    any invoice by pairing their own order with somebody else's number."""
    other_org = Organisation.objects.create(name="Somebody Else")
    other_user = User.objects.create_user(
        email="other@example.com", password=PASSWORD, email_verified_at=timezone.now()
    )
    Membership.objects.create(user=other_user, organisation=other_org)
    other_order = services.convert_enquiry(
        enquiry=Enquiry.objects.create(
            organisation=other_org, submitted_by=other_user,
            problem="A different problem entirely.",
        ),
        actor=staff, title="Their work", scope="Their scope.",
    )
    contract = services.issue_contract(order=other_order, actor=staff)
    services.record_signature(
        contract=contract, actor=staff, signed_on=timezone.localdate(),
        signed_by_name="Them", note="Filed.",
    )
    theirs = services.issue_invoice(
        order=other_order, actor=staff, description="Theirs",
        amount_kes=Decimal("999.00"),
    )

    client.force_login(client_user)
    response = client.get(
        reverse("invoice-document", args=[signed.reference, theirs.number])
    )
    assert response.status_code == 404


def test_an_anonymous_visitor_gets_nothing(client, signed, staff):
    invoice = services.issue_invoice(
        order=signed, actor=staff, milestone=signed.milestones.first()
    )
    response = client.get(
        reverse("invoice-document", args=[signed.reference, invoice.number])
    )
    assert response.status_code in {401, 403}


def test_the_document_is_addressed_to_the_organisation_not_our_own_contact(
    client, signed, staff, client_user
):
    """
    `order.contact` is the GENMARS point of contact (Charter 05 §I). Rendering
    it under "To" made the invoice read "To: Kilimani Dental, Ops" — as though
    we were billing our own staff member.
    """
    invoice = services.issue_invoice(
        order=signed, actor=staff, milestone=signed.milestones.first()
    )
    client.force_login(client_user)
    body = client.get(
        reverse("invoice-document", args=[signed.reference, invoice.number])
    ).json()

    assert body["billed_to"]["organisation"] == "Kilimani Dental"
    # Whoever it is addressed to, it is not a Genmars employee.
    assert staff.full_name not in str(body["billed_to"])
    assert staff.email not in str(body["billed_to"])


# ── payments add up ──────────────────────────────────────────────────────────
#
# M-Pesa caps a single transaction, so a large invoice is routinely settled by
# several transfers with several codes. The old shape stored one reference and
# overwrote it, so the second payment made the invoice claim it had been
# settled by a payment covering a fraction of it.


def test_a_part_payment_leaves_the_invoice_outstanding(signed, staff):
    invoice = services.issue_invoice(
        order=signed, actor=staff, milestone=signed.milestones.first()
    )

    services.record_payment(
        invoice=invoice, actor=staff, amount_kes=Decimal("50000.00"),
        method="mpesa", reference="SLJ7XK2P1Q",
    )

    invoice.refresh_from_db()
    assert invoice.status == Invoice.Status.ISSUED
    assert invoice.amount_paid == Decimal("50000.00")
    assert invoice.balance == Decimal("100000.00")
    assert invoice.paid_on is None


def test_payments_accumulate_until_the_invoice_is_settled(signed, staff):
    """Three codes, one invoice. It is paid when they add up and not before."""
    invoice = services.issue_invoice(
        order=signed, actor=staff, milestone=signed.milestones.first()
    )

    for amount, code in [
        (Decimal("70000.00"), "SLJ7XK2P1Q"),
        (Decimal("70000.00"), "SLJ8YM4R3T"),
    ]:
        services.record_payment(
            invoice=invoice, actor=staff, amount_kes=amount,
            method="mpesa", reference=code,
        )
        invoice.refresh_from_db()
        assert invoice.status == Invoice.Status.ISSUED, "settled too early"

    services.record_payment(
        invoice=invoice, actor=staff, amount_kes=Decimal("10000.00"),
        method="bank", reference="FT26091200881",
    )

    invoice.refresh_from_db()
    assert invoice.status == Invoice.Status.PAID
    assert invoice.balance == Decimal("0.00")
    assert invoice.payments.count() == 3
    # Every reference is on the invoice, so it can be checked against a
    # statement without opening the payment rows.
    for code in ["SLJ7XK2P1Q", "SLJ8YM4R3T", "FT26091200881"]:
        assert code in invoice.payment_reference


def test_recording_more_than_is_owed_is_refused(signed, staff):
    """
    Almost always a typo in the amount. The version that is not a typo needs a
    person, not a row that silently absorbs it.
    """
    invoice = services.issue_invoice(
        order=signed, actor=staff, milestone=signed.milestones.first()
    )
    services.record_payment(
        invoice=invoice, actor=staff, amount_kes=Decimal("100000.00"),
        method="mpesa", reference="SLJ7XK2P1Q",
    )

    with pytest.raises(services.OperationsError) as caught:
        services.record_payment(
            invoice=invoice, actor=staff, amount_kes=Decimal("60000.00"),
            method="mpesa", reference="SLJ8YM4R3T",
        )

    assert "more than is outstanding" in str(caught.value)
    invoice.refresh_from_db()
    assert invoice.payments.count() == 1
    assert invoice.status == Invoice.Status.ISSUED


def test_the_same_mpesa_code_cannot_pay_two_invoices(signed, staff):
    """
    An M-Pesa code is unique across the network. The same one appearing twice
    means a payment was recorded twice — which shows the company as having been
    paid money it was never paid.
    """
    first = services.issue_invoice(
        order=signed, actor=staff, milestone=signed.milestones.first()
    )
    second = services.issue_invoice(
        order=signed, actor=staff, milestone=signed.milestones.last()
    )

    services.record_payment(
        invoice=first, actor=staff, amount_kes=Decimal("150000.00"),
        method="mpesa", reference="SLJ7XK2P1Q",
    )

    with pytest.raises(services.OperationsError) as caught:
        services.record_payment(
            invoice=second, actor=staff, amount_kes=Decimal("100000.00"),
            method="mpesa", reference="SLJ7XK2P1Q",
        )

    assert "already recorded" in str(caught.value)
    assert second.payments.count() == 0


def test_a_payment_with_no_reference_is_refused_unless_it_is_cash(signed, staff):
    invoice = services.issue_invoice(
        order=signed, actor=staff, milestone=signed.milestones.first()
    )

    with pytest.raises(services.OperationsError):
        services.record_payment(
            invoice=invoice, actor=staff, amount_kes=Decimal("1000.00"),
            method="bank", reference="",
        )

    # Cash is the one case where a human was already in the room.
    services.record_payment(
        invoice=invoice, actor=staff, amount_kes=Decimal("1000.00"),
        method="cash", reference="",
    )
    assert invoice.payments.count() == 1


# ── invoices raised straight to a client ─────────────────────────────────────


def test_a_direct_invoice_needs_no_order_but_does_need_a_client_we_know(staff):
    """
    Anyone can be added as an organisation. That is not the same as being
    someone we may send a bill to.
    """
    stranger = Organisation.objects.create(name="Someone We Have Never Met")

    with pytest.raises(services.OperationsError) as caught:
        services.issue_direct_invoice(
            organisation=stranger, actor=staff,
            description="Consultancy", amount_kes=Decimal("40000.00"),
        )

    assert "no orders and no previous invoices" in str(caught.value)
    assert Invoice.objects.count() == 0


def test_a_past_client_can_be_invoiced_without_a_contract(signed, staff):
    """
    The signed-contract guard is right for project work and wrong for an
    afternoon's work for someone we already deliver to. Separate door, separate
    guard — see issue_direct_invoice.
    """
    invoice = services.issue_direct_invoice(
        organisation=signed.organisation, actor=staff,
        description="Domain renewal paid on their behalf",
        amount_kes=Decimal("2400.00"),
    )

    assert invoice.order_id is None
    assert invoice.is_direct
    assert invoice.organisation == signed.organisation
    assert invoice.amount_kes == Decimal("2400.00")


def test_a_direct_invoice_cannot_smuggle_a_milestone_in(signed, staff):
    """The database refuses it too — see the check constraint on Invoice."""
    invoice = services.issue_direct_invoice(
        organisation=signed.organisation, actor=staff,
        description="Ad hoc", amount_kes=Decimal("5000.00"),
    )
    assert invoice.milestone_id is None


# ── notifications ────────────────────────────────────────────────────────────


def test_issuing_an_invoice_notifies_everyone_on_the_client_account(signed, staff, client_user):
    """
    One row per person, not one per event. "Read" is a fact about a person, and
    a shared row means one colleague opening it marks it read for everyone.
    """
    from portal.models import Notification

    colleague = User.objects.create_user(
        email="finance@example.com", password=PASSWORD, full_name="Finance",
        email_verified_at=timezone.now(),
    )
    Membership.objects.create(user=colleague, organisation=signed.organisation)

    invoice = services.issue_invoice(
        order=signed, actor=staff, milestone=signed.milestones.first()
    )

    rows = Notification.objects.filter(kind=Notification.Kind.INVOICE_ISSUED)
    assert set(rows.values_list("user__email", flat=True)) == {
        client_user.email, colleague.email,
    }
    assert all(r.audience == Notification.Audience.CLIENT for r in rows)
    assert invoice.number in rows.first().title
    # Staff are not notified on a client surface, and never the other way.
    assert not rows.filter(user__is_staff=True).exists()


def test_a_client_notification_never_reaches_the_staff_surface(signed, staff):
    from portal.models import Notification

    services.issue_invoice(
        order=signed, actor=staff, milestone=signed.milestones.first()
    )
    assert not Notification.objects.filter(
        audience=Notification.Audience.STAFF
    ).exists()
