"""
The activity log.

Append-only, and the tests that matter are about what must NOT end up in it.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from accounts.models import Membership, Organisation, User
from operations import services
from portal.models import ActivityLog, Enquiry, Milestone

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
def signed(staff) -> "object":
    org = Organisation.objects.create(name="Kilimani Dental")
    client_user = User.objects.create_user(
        email="mercy@example.com", password=PASSWORD, email_verified_at=timezone.now()
    )
    Membership.objects.create(user=client_user, organisation=org)
    enquiry = Enquiry.objects.create(
        organisation=org, submitted_by=client_user, problem="Reconciling by hand."
    )
    order = services.convert_enquiry(
        enquiry=enquiry, actor=staff, title="Reconciliation", scope="Do the thing."
    )
    Milestone.objects.create(
        order=order, name="On signature", amount_kes=Decimal("150000.00"), position=1
    )
    contract = services.issue_contract(order=order, actor=staff)
    services.record_signature(
        contract=contract, actor=staff, signed_on=timezone.localdate(),
        signed_by_name="Dr Wanjiku", note="Countersigned.",
    )
    return order


def test_money_moving_is_written_down(signed, staff):
    invoice = services.issue_invoice(
        order=signed, actor=staff, milestone=signed.milestones.first()
    )
    services.record_payment(
        invoice=invoice, actor=staff, method="mpesa", reference="SLJ7XK2P1Q",
    )

    actions = list(
        ActivityLog.objects.order_by("id").values_list("action", flat=True)
    )
    assert ActivityLog.Action.INVOICE_ISSUED in actions
    assert ActivityLog.Action.INVOICE_PAID in actions

    issued = ActivityLog.objects.get(action=ActivityLog.Action.INVOICE_ISSUED)
    assert issued.subject == invoice.number
    assert issued.actor == staff
    assert issued.organisation == signed.organisation
    # The name survives the account being deleted later — SET_NULL loses the FK.
    assert issued.actor_label == "Ops Person"


def test_a_part_payment_and_a_settlement_are_different_entries(signed, staff):
    invoice = services.issue_invoice(
        order=signed, actor=staff, milestone=signed.milestones.first()
    )
    services.record_payment(
        invoice=invoice, actor=staff, amount_kes=Decimal("50000.00"),
        method="mpesa", reference="SLJ7XK2P1Q",
    )
    services.record_payment(
        invoice=invoice, actor=staff, amount_kes=Decimal("100000.00"),
        method="bank", reference="FT26091200881",
    )

    assert ActivityLog.objects.filter(
        action=ActivityLog.Action.PAYMENT_RECORDED
    ).count() == 1
    assert ActivityLog.objects.filter(
        action=ActivityLog.Action.INVOICE_PAID
    ).count() == 1


def test_the_system_acting_is_recorded_as_the_system(signed, staff):
    """An M-Pesa callback has no actor, and "somebody" would be a lie."""
    invoice = services.issue_invoice(
        order=signed, actor=staff, milestone=signed.milestones.first()
    )
    services.record_payment(
        invoice=invoice, actor=None, method="mpesa", reference="SLJ7XK2P1Q",
    )

    entry = ActivityLog.objects.filter(
        action=ActivityLog.Action.INVOICE_PAID
    ).first()
    assert entry.actor is None
    assert entry.actor_label == "System"


def test_a_failure_to_log_never_loses_the_thing_being_logged(signed, staff, monkeypatch):
    """
    The invoice is the fact; the log is the account of it. An invoice issued
    correctly must not be rolled back because a log row could not be written.
    """
    def boom(*args, **kwargs):
        raise RuntimeError("log table on fire")

    monkeypatch.setattr(ActivityLog.objects, "create", boom)

    invoice = services.issue_invoice(
        order=signed, actor=staff, milestone=signed.milestones.first()
    )
    assert invoice.pk is not None


def test_no_verification_code_ever_reaches_the_log(signed, staff):
    """
    The log is where a secret would live forever on disk, readable by everyone
    with operations access, and end up in every backup. This walks every entry
    looking for anything code-shaped.
    """
    import re

    invoice = services.issue_invoice(
        order=signed, actor=signed and staff, milestone=signed.milestones.first()
    )
    services.record_payment(
        invoice=invoice, actor=staff, method="mpesa", reference="SLJ7XK2P1Q",
    )

    for entry in ActivityLog.objects.all():
        blob = f"{entry.summary} {entry.detail}"
        # Six standalone digits is the shape of a verification code. Money is
        # written with decimals and separators, so it does not trip this.
        assert not re.search(r"(?<![\d.,])\d{6}(?![\d.,])", blob), blob


def test_the_log_is_append_only_in_practice(signed, staff):
    """
    Nothing in the codebase updates or deletes an entry. This is a guard
    against that changing quietly.
    """
    import subprocess

    result = subprocess.run(
        ["grep", "-rn", "-E",
         r"ActivityLog\.objects\.(filter|all|get)\([^)]*\)\.(update|delete)|ActivityLog\.objects\.(update|delete)",
         "operations", "portal", "accounts"],
        capture_output=True, text=True,
    )
    assert result.stdout.strip() == "", result.stdout


# ── price changes ────────────────────────────────────────────────────────────


def test_a_price_change_records_both_numbers(staff):
    """
    "When did this become 75,000" is a question somebody asks six months later.
    Recording only the new value cannot answer it.
    """
    from decimal import Decimal as D

    from portal.models import Service, ServiceTier

    service = Service.objects.create(
        name="Implementation", slug="implementation", summary="Setup.",
        price_unit="one-time",
    )
    tier = ServiceTier.objects.create(
        service=service, slug="business-setup", name="Business Setup",
        price_kes=D("75000.00"), published_price_kes=D("75000.00"),
        lead="Real data to migrate.", includes="Discovery", position=2,
    )

    services.set_tier_price(tier=tier, actor=staff, price_kes=D("85000.00"))

    entry = ActivityLog.objects.get(action=ActivityLog.Action.PRICE_CHANGED)
    assert entry.detail["was"] == "75000.00"
    assert entry.detail["now"] == "85000.00"
    # And what the public page still says, so the gap is in the record too.
    assert entry.detail["website_says"] == "75000.00"
    assert "75,000.00 to KES 85,000.00" in entry.summary


def test_a_zero_price_is_refused(staff):
    from decimal import Decimal as D

    from portal.models import Service, ServiceTier

    service = Service.objects.create(name="X", slug="x", summary="x")
    tier = ServiceTier.objects.create(
        service=service, slug="t", name="T", price_kes=D("100.00"),
        lead="l", includes="i",
    )

    with pytest.raises(services.OperationsError):
        services.set_tier_price(tier=tier, actor=staff, price_kes=D("0.00"))


def test_setting_the_same_price_writes_nothing(staff):
    """A log full of no-ops is a log nobody reads."""
    from decimal import Decimal as D

    from portal.models import Service, ServiceTier

    service = Service.objects.create(name="X", slug="x", summary="x")
    tier = ServiceTier.objects.create(
        service=service, slug="t", name="T", price_kes=D("100.00"),
        lead="l", includes="i",
    )

    services.set_tier_price(tier=tier, actor=staff, price_kes=D("100.00"))
    assert not ActivityLog.objects.filter(
        action=ActivityLog.Action.PRICE_CHANGED
    ).exists()
