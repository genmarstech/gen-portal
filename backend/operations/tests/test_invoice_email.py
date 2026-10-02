"""
Telling a client they have been billed.

═══════════════════════════════════════════════════════════════════════════════
IT TOLD THEM NOTHING.

An order, a contract, a signature and an offer all reach the client by email.
An invoice wrote a dashboard notification and stopped — so Genmars billed
people and relied on them signing in to find out. For a client who logs in
once a quarter that is an invoice sitting unseen until somebody telephones
about it, and an unseen invoice is an unpaid invoice.

The other half of this file is about what the message deliberately does NOT
carry.
═══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.utils import timezone

from accounts.models import Membership, Organisation, User
from operations import services
from portal.models import Enquiry

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
def org() -> Organisation:
    return Organisation.objects.create(name="Clips Serenity Spa")


@pytest.fixture
def owner(org) -> User:
    person = User.objects.create_user(
        email="owner@spa.co.ke", password=PASSWORD, full_name="The owner",
        email_verified_at=timezone.now(),
    )
    Membership.objects.create(user=person, organisation=org, receives_updates=True)
    return person


@pytest.fixture
def order(org, staff, owner, mailoutbox):
    """
    An order that may actually be billed.

    Charter 02 §I: no invoice without a signed statement of work. The
    contract and the signature both email the client too, so the outbox is
    cleared — every test below is about the INVOICE message.
    """
    enquiry = Enquiry.objects.create(
        organisation=org, submitted_by=owner, problem="Bookings get missed.",
        status=Enquiry.Status.QUALIFYING,
    )
    order = services.convert_enquiry(
        enquiry=enquiry, actor=staff, title="Online booking",
        scope="Add online booking.", tell_client=False,
    )
    contract = services.issue_contract(order=order, actor=staff)
    services.record_signature(
        contract=contract, actor=staff, signed_on=timezone.localdate(),
        signed_by_name="The owner", note="Signed.",
    )
    mailoutbox.clear()
    return order


def bill(order, staff, amount="75000.00"):
    return services.issue_invoice(
        order=order, actor=staff, description="Phase one",
        amount_kes=Decimal(amount),
    )


def test_issuing_an_invoice_emails_the_client(order, staff, owner, mailoutbox):
    """
    ══════════════════════════════════════════════════════════════════════
    THE ONE THAT MATTERS. This sent nothing before.
    ══════════════════════════════════════════════════════════════════════
    """
    invoice = bill(order, staff)

    assert len(mailoutbox) == 1
    sent = mailoutbox[0]
    assert sent.to == ["owner@spa.co.ke"]
    assert invoice.number in sent.subject
    assert "75,000.00" in sent.subject
    assert "Phase one" in sent.body


def test_the_email_carries_no_payment_details(order, staff, owner, mailoutbox):
    """
    ══════════════════════════════════════════════════════════════════════
    NO PAYBILL, NO ACCOUNT NUMBER, NO BANK.

    Invoice redirection fraud works by intercepting exactly this message
    and changing exactly those digits, and it works because the client has
    no reason to doubt an invoice that looks like the last one. It is the
    most common way a small business in Kenya loses a five-figure payment,
    and the money does not come back.

    The authoritative details live behind a sign-in. This asserts the
    message does not quietly grow them later.
    ══════════════════════════════════════════════════════════════════════
    """
    bill(order, staff)
    body = mailoutbox[0].body.lower()

    for leak in ("paybill", "account number", "a/c", "swift", "iban",
                 "till number", "account name"):
        assert leak not in body

    # And it teaches the client the rule, so the one time it matters they
    # have read it a dozen times already.
    assert "never send you bank or m-pesa details by email" in body


def test_a_part_payment_does_not_read_as_settled(order, staff, owner, mailoutbox):
    """
    "Payment received" on an invoice still half outstanding stops the
    client thinking about it, and the reminder that follows then looks like
    a mistake on our side. What is left goes in the subject line.
    """
    invoice = bill(order, staff, "100000.00")
    mailoutbox.clear()

    services.record_payment(
        invoice=invoice, actor=staff, amount_kes=Decimal("40000.00"),
        paid_on=timezone.localdate(), method="bank", reference="TRX1",
    )

    sent = mailoutbox[0]
    assert "60,000.00" in sent.subject
    assert "still outstanding" in sent.subject.lower()
    assert "paid in full" not in sent.subject.lower()


def test_settling_it_says_so(order, staff, owner, mailoutbox):
    invoice = bill(order, staff, "50000.00")
    mailoutbox.clear()

    services.record_payment(
        invoice=invoice, actor=staff, amount_kes=Decimal("50000.00"),
        paid_on=timezone.localdate(), method="mpesa", reference="TRX2",
    )

    assert "paid in full" in mailoutbox[0].subject.lower()


def test_nobody_unverified_or_unsubscribed_is_billed_by_email(
    order, staff, org, owner, mailoutbox
):
    """
    The same two exclusions every client email here observes. A dashboard
    row has neither problem — it is behind the client's own sign-in.
    """
    quiet = User.objects.create_user(
        email="quiet@spa.co.ke", password=PASSWORD,
        email_verified_at=timezone.now(),
    )
    Membership.objects.create(user=quiet, organisation=org, receives_updates=False)

    unverified = User.objects.create_user(email="unproven@spa.co.ke", password=PASSWORD)
    Membership.objects.create(user=unverified, organisation=org, receives_updates=True)

    bill(order, staff)

    assert [m.to for m in mailoutbox] == [["owner@spa.co.ke"]]


def test_a_dead_relay_does_not_undo_the_invoice(order, staff, owner, mailoutbox):
    """
    The invoice is the fact; the email is an account of it. A mail provider
    having a bad minute must not lose a bill — and the dashboard row has
    already landed either way.
    """
    from unittest.mock import patch

    from portal.models import Invoice

    with patch(
        "accounts.emails.send_invoice_issued", side_effect=RuntimeError("relay down")
    ):
        invoice = bill(order, staff)

    assert Invoice.objects.filter(pk=invoice.pk).exists()
    assert mailoutbox == []
