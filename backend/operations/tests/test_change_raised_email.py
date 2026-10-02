"""
Genmars being told a client has asked for something.

═══════════════════════════════════════════════════════════════════════════════
IT WAS A DASHBOARD ROW AND NOTHING ELSE.

A client saying "this is wrong, please change it before you build it" reached
Genmars only if somebody happened to be looking at ops. For the one message
whose entire value is arriving BEFORE work starts, that is the worst place to
leave it — the client has done exactly what the order email asked them to do
and the reply comes a week later, after the thing has been built.
═══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import pytest
from django.utils import timezone

from accounts.models import Membership, Organisation, User
from operations import services
from portal.models import ChangeRequest, Enquiry

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
def other_staff() -> User:
    return User.objects.create_user(
        email="someone.else@genmars.co.ke", password=PASSWORD, full_name="Else",
        is_staff=True, email_verified_at=timezone.now(),
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


def raise_one(order, owner, **extra):
    return services.raise_change_request(
        order=order, actor=owner,
        summary=extra.pop("summary", "Is anything missing from the scope?"),
        detail=extra.pop("detail", "We also take walk-ins and they are not mentioned."),
        **extra,
    )


def test_the_order_contact_is_emailed(order, owner, founder, mailoutbox):
    """
    ══════════════════════════════════════════════════════════════════════
    THE ONE THAT MATTERS. This sent nothing before.
    ══════════════════════════════════════════════════════════════════════
    """
    change = raise_one(order, owner)

    assert len(mailoutbox) == 1
    sent = mailoutbox[0]
    assert sent.to == [founder.email]
    assert order.reference in sent.subject
    assert "Clips Serenity Spa" in sent.subject
    assert change.reference in sent.body


def test_the_clients_own_words_are_in_the_message(order, owner, mailoutbox):
    """
    Not behind a link. Whoever reads this on a phone should be able to tell
    in five seconds whether it needs answering today.
    """
    raise_one(order, owner, detail="We also take walk-ins and they are not mentioned.")
    assert "walk-ins" in mailoutbox[0].body


def test_it_does_not_read_as_a_commitment(order, owner, mailoutbox):
    """
    A raised change is unclassified and unpriced. An email that read like an
    instruction would have the company agreeing to work by notification —
    the same trap send_order_opened documents.
    """
    raise_one(order, owner)
    body = mailoutbox[0].body.lower()
    assert "nothing has been classified or priced yet" in body
    for claim in ("we will add", "approved", "we have agreed"):
        assert claim not in body


def test_it_goes_to_the_named_contact_and_not_to_every_member_of_staff(
    order, owner, founder, other_staff, mailoutbox
):
    """
    Charter 05 §I makes Order.contact the person responsible, so it already
    answers "whose is this". Broadcasting is how a team learns to ignore a
    channel: the third person to receive something they cannot act on stops
    reading the second.

    The dashboard row still goes to everybody — a list somebody chooses to
    open is a different thing from a message that arrives.
    """
    raise_one(order, owner)

    assert [m.to for m in mailoutbox] == [[founder.email]]
    assert other_staff.notifications.filter(
        kind="change_raised"
    ).exists() or other_staff.notifications.exists()


def test_a_dead_relay_does_not_lose_what_the_client_asked_for(
    order, owner, mailoutbox
):
    """
    The change request is the fact; the email is an account of it. Losing a
    client's words because a mail provider had a bad minute is the one
    outcome worse than not emailing at all.
    """
    from unittest.mock import patch

    with patch(
        "accounts.emails.send_change_raised", side_effect=RuntimeError("relay down")
    ):
        change = raise_one(order, owner)

    assert ChangeRequest.objects.filter(pk=change.pk).exists()
    assert mailoutbox == []
