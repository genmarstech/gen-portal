"""
Starting work on an order.

═══════════════════════════════════════════════════════════════════════════════
NOTHING COULD DO THIS BEFORE.

`Order.Status` has had ACTIVE, REVIEW and CLOSED since it was written and
nothing in the application ever assigned any of them. An order was created
SCOPING and stayed SCOPING for ever — so "in progress" was a label on a
dropdown, the delivery board showed every live engagement in one column, and a
client reading their own order page was told work had not started on something
that shipped in March.

The two guards are the point of the transition, not decoration on it: Charter
02 §I's signed statement of work, and the client having had the chance to say
the scope is wrong before anybody builds it.
═══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import pytest
from django.utils import timezone

from accounts.models import Membership, Organisation, User
from operations import services
from portal.models import ActivityLog, Enquiry, Order, OrderSeen

pytestmark = pytest.mark.django_db

PASSWORD = "correct-horse-battery"


@pytest.fixture
def founder() -> User:
    return User.objects.create_user(
        email="founder@genmars.co.ke", password=PASSWORD, full_name="The founder",
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
def order(org, founder, owner, mailoutbox):
    enquiry = Enquiry.objects.create(
        organisation=org, submitted_by=owner, problem="Bookings get missed.",
        status=Enquiry.Status.QUALIFYING,
    )
    order = services.convert_enquiry(
        enquiry=enquiry, actor=founder, title="Online booking",
        scope="Add online booking.", tell_client=False,
    )
    mailoutbox.clear()
    return order


def sign(order, founder):
    contract = services.issue_contract(order=order, actor=founder)
    services.record_signature(
        contract=contract, actor=founder, signed_on=timezone.localdate(),
        signed_by_name="The owner", note="Signed.",
    )
    return contract


def looked(order, owner):
    OrderSeen.objects.create(user=owner, order=order)


# ── the guards ───────────────────────────────────────────────────────────────


def test_work_cannot_start_without_a_signed_statement_of_work(
    order, founder, owner, mailoutbox
):
    """
    ══════════════════════════════════════════════════════════════════════
    Charter 02 §I. `issue_invoice` already refuses without one — "the
    agreement comes before the work, and before the bill". Refusing to BILL
    without a contract while allowing the work itself enforces the weaker
    half of that sentence and leaves the stronger half to memory.
    ══════════════════════════════════════════════════════════════════════
    """
    looked(order, owner)
    with pytest.raises(services.OperationsError) as refusal:
        services.start_order(order=order, actor=founder)

    assert "statement of work" in str(refusal.value)
    order.refresh_from_db()
    assert order.status == Order.Status.SCOPING
    assert mailoutbox == []


def test_work_cannot_start_before_the_client_has_opened_the_order(
    order, founder, owner, mailoutbox
):
    """
    The order email asks them to check the scope. The order page asks them
    the questions. Both are worth nothing if work starts before anybody
    looked — the gap between opening an order and starting it exists so the
    client can object, and starting while they are unaware closes the gap
    without using it.
    """
    sign(order, founder)
    mailoutbox.clear()

    with pytest.raises(services.OperationsError) as refusal:
        services.start_order(order=order, actor=founder)

    assert "has opened this order yet" in str(refusal.value)
    # The field is the stable part — a screen hangs its error on it.
    assert refusal.value.field == "despite_unseen"
    order.refresh_from_db()
    assert order.status == Order.Status.SCOPING


def test_it_can_be_started_anyway_with_a_reason(order, founder, owner, mailoutbox):
    """
    ══════════════════════════════════════════════════════════════════════
    NOT AN ABSOLUTE BAR, BECAUSE THAT WOULD BE WORSE.

    A client who agreed everything on the telephone and will never sign in
    would otherwise block their own work indefinitely — and the pressure to
    get round that produces a status set directly in the database with no
    record at all. The override costs a sentence and is auditable.
    ══════════════════════════════════════════════════════════════════════
    """
    sign(order, founder)
    services.start_order(
        order=order, actor=founder,
        despite_unseen="Agreed on the phone on the 2nd; they do not use the portal.",
    )

    order.refresh_from_db()
    assert order.status == Order.Status.ACTIVE

    entry = ActivityLog.objects.get(action=ActivityLog.Action.ORDER_STARTED)
    assert "do not use the portal" in entry.summary


def test_the_ordinary_path(order, founder, owner, mailoutbox):
    """The control. Every refusal above needs this to pass first."""
    sign(order, founder)
    looked(order, owner)
    mailoutbox.clear()

    services.start_order(order=order, actor=founder)

    order.refresh_from_db()
    assert order.status == Order.Status.ACTIVE
    assert order.started_on == timezone.localdate()


# ── what it does ─────────────────────────────────────────────────────────────


def test_the_client_is_told_and_this_email_may_say_work_started(
    order, founder, owner, mailoutbox, django_capture_on_commit_callbacks
):
    """
    `send_order_opened` goes to lengths NOT to say work has started — an
    order opens in SCOPING, often minutes after a phone call. This is the
    other side of that line: a contract is signed, the client has had their
    chance to object, and the sentence is now simply true.
    """
    sign(order, founder)
    looked(order, owner)
    mailoutbox.clear()

    with django_capture_on_commit_callbacks(execute=True):
        services.start_order(order=order, actor=founder)

    assert len(mailoutbox) == 1
    sent = mailoutbox[0]
    assert sent.to == ["owner@spa.co.ke"]
    assert "we have started" in sent.subject.lower()
    assert "has started" in sent.body.lower()


def test_an_order_cannot_be_started_twice(order, founder, owner):
    sign(order, founder)
    looked(order, owner)
    services.start_order(order=order, actor=founder)

    with pytest.raises(services.OperationsError) as refusal:
        services.start_order(order=order, actor=founder)
    assert "already" in str(refusal.value)


def test_a_real_start_date_is_not_overwritten_with_today(
    order, founder, owner
):
    """
    Work recorded retrospectively carries its real start date. Stamping
    today would rewrite history to the date somebody got round to pressing
    the button.
    """
    from datetime import timedelta

    was = timezone.localdate() - timedelta(days=40)
    Order.objects.filter(pk=order.pk).update(started_on=was)
    order.refresh_from_db()

    sign(order, founder)
    looked(order, owner)
    services.start_order(order=order, actor=founder)

    order.refresh_from_db()
    assert order.started_on == was


def test_nothing_is_started_if_the_email_cannot_be_sent(
    order, founder, owner, mailoutbox, django_capture_on_commit_callbacks
):
    """
    The other way round, in fact: the order IS started and the email is
    lost. Starting work is the fact; telling them is an account of it, and
    a dead relay must not leave the delivery board disagreeing with
    reality.
    """
    from unittest.mock import patch

    sign(order, founder)
    looked(order, owner)

    with patch(
        "accounts.emails.send_order_started", side_effect=RuntimeError("relay down")
    ):
        with django_capture_on_commit_callbacks(execute=True):
            services.start_order(order=order, actor=founder)

    order.refresh_from_db()
    assert order.status == Order.Status.ACTIVE
