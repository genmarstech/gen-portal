"""
Telling the client when their enquiry becomes an order.

═══════════════════════════════════════════════════════════════════════════════
IT DID NOT.

`create_order` — the path for work Genmars opens itself — has emailed the
client since it was written. `convert_enquiry` — the path a client's OWN
enquiry travels down, which is most of them — created the order, wrote the
activity log, and said nothing to the person who had asked. They heard back
when somebody remembered to write to them.

Charter 05 §I wants scope agreed in writing before work begins, and the whole
value of writing it down early is that the client gets to disagree while
disagreeing is cheap. An order nobody told them about cannot be disagreed
with.
═══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import pytest
from django.utils import timezone

from accounts.models import Membership, Organisation, User
from operations import services
from portal.models import Enquiry, Order

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
def client_user(org) -> User:
    person = User.objects.create_user(
        email="owner@spa.co.ke", password=PASSWORD, full_name="The owner",
        email_verified_at=timezone.now(),
    )
    Membership.objects.create(user=person, organisation=org, receives_updates=True)
    return person


@pytest.fixture
def enquiry(org, client_user) -> Enquiry:
    return Enquiry.objects.create(
        organisation=org,
        submitted_by=client_user,
        problem="Bookings are taken on WhatsApp and get missed.",
        status=Enquiry.Status.QUALIFYING,
    )


def convert(enquiry, staff, callbacks, **extra):
    with callbacks(execute=True):
        return services.convert_enquiry(
            enquiry=enquiry, actor=staff, title="Online booking",
            scope="Add online booking, with SMS confirmation.", **extra
        )


def test_converting_an_enquiry_tells_the_client(
    enquiry, staff, client_user, mailoutbox, django_capture_on_commit_callbacks
):
    """
    ══════════════════════════════════════════════════════════════════════
    THE ONE THAT MATTERS. This sent nothing at all before.
    ══════════════════════════════════════════════════════════════════════
    """
    order = convert(enquiry, staff, django_capture_on_commit_callbacks)

    assert len(mailoutbox) == 1
    sent = mailoutbox[0]
    assert sent.to == ["owner@spa.co.ke"]
    assert order.reference in sent.subject
    # The scope is IN the message — the point of writing it down is that the
    # client can disagree while disagreeing is cheap.
    assert "sms confirmation" in sent.body.lower()


def test_it_does_not_claim_work_has_started(
    enquiry, staff, client_user, mailoutbox, django_capture_on_commit_callbacks
):
    """
    Charter 02 §I puts a signed statement of work before delivery. An order
    opens in SCOPING, often minutes after a phone call, and "we've started"
    would commit the company by notification instead of by contract.
    """
    convert(enquiry, staff, django_capture_on_commit_callbacks)
    body = mailoutbox[0].body.lower()
    assert "nothing has started yet" in body
    for claim in ("we have begun", "we've started", "work has started"):
        assert claim not in body


def test_an_operator_can_choose_not_to_send_it(
    enquiry, staff, client_user, mailoutbox, django_capture_on_commit_callbacks
):
    """
    Converting an enquiry somebody has already been spoken to about at
    length should not force a second telling of the same thing.
    """
    convert(enquiry, staff, django_capture_on_commit_callbacks, tell_client=False)
    assert mailoutbox == []


def test_nobody_unverified_or_unsubscribed_is_written_to(
    staff, org, mailoutbox, django_capture_on_commit_callbacks
):
    """
    The same two exclusions every client email here observes.
    `receives_updates` off means they asked not to hear about this, and an
    unverified address is one nobody has proved they read — sending a
    client's scope to it would be sending it to whoever owns that mailbox.
    """
    quiet = User.objects.create_user(
        email="quiet@spa.co.ke", password=PASSWORD, email_verified_at=timezone.now()
    )
    enquiry = Enquiry.objects.create(
        organisation=org, submitted_by=quiet,
        problem="Bookings get missed.", status=Enquiry.Status.QUALIFYING,
    )
    Membership.objects.create(user=quiet, organisation=org, receives_updates=False)

    unverified = User.objects.create_user(
        email="unproven@spa.co.ke", password=PASSWORD
    )
    Membership.objects.create(
        user=unverified, organisation=org, receives_updates=True
    )

    convert(enquiry, staff, django_capture_on_commit_callbacks)
    assert mailoutbox == []


def test_nothing_is_sent_for_an_order_that_was_rolled_back(
    enquiry, staff, client_user, mailoutbox, django_capture_on_commit_callbacks
):
    """
    ══════════════════════════════════════════════════════════════════════
    AN EMAIL CANNOT BE ROLLED BACK.

    Both order paths are @transaction.atomic and create_order used to send
    INLINE. Anything failing after the send — in the rest of the function,
    or in a view or a job that wrapped it in a larger transaction — would
    roll the order back and leave the client holding a message about work
    that does not exist, quoting a reference nobody at Genmars can find.

    `transaction.on_commit` is what makes that impossible, and this is the
    test that says so: the outer transaction raises, the order is gone, and
    so is the email.
    ══════════════════════════════════════════════════════════════════════
    """
    from django.db import transaction

    class Deliberate(Exception):
        pass

    with pytest.raises(Deliberate):
        with transaction.atomic():
            services.convert_enquiry(
                enquiry=enquiry, actor=staff, title="Online booking",
                scope="Add online booking.",
            )
            raise Deliberate

    assert not Order.objects.exists()
    assert mailoutbox == []


def test_a_failed_email_does_not_undo_the_order(
    enquiry, staff, client_user, mailoutbox, django_capture_on_commit_callbacks
):
    """
    The order is the fact; the email is an account of it. A mail provider
    having a bad minute must not lose the conversion — and the enquiry must
    still be marked converted, or the next person to look at it converts it
    again.
    """
    from unittest.mock import patch

    with patch(
        "accounts.emails.send_order_opened", side_effect=RuntimeError("relay down")
    ):
        order = convert(enquiry, staff, django_capture_on_commit_callbacks)

    enquiry.refresh_from_db()
    assert Order.objects.filter(pk=order.pk).exists()
    assert enquiry.status == Enquiry.Status.CONVERTED
    assert enquiry.converted_to_id == order.pk
