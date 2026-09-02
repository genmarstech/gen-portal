"""
Support.

The test that matters here is test_an_internal_note_never_reaches_the_client.
Everything else in this file can fail and cost a bug. That one failing means a
client reads what we wrote about them privately — worse than a data leak,
because it is a leak of opinion.
"""

from __future__ import annotations

import pytest
from django.core import mail
from django.urls import reverse
from django.utils import timezone

from accounts.models import Membership, Organisation, User
from operations import services
from portal.models import SupportMessage, SupportTicket

pytestmark = pytest.mark.django_db

PASSWORD = "correct-horse-battery"


@pytest.fixture(autouse=True)
def _no_throttle(settings):
    settings.REST_FRAMEWORK = {
        **settings.REST_FRAMEWORK,
        "DEFAULT_THROTTLE_RATES": {
            **settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"],
            "enquiry": "1000/hour",
        },
    }


@pytest.fixture
def staff() -> User:
    return User.objects.create_user(
        email="ops@genmars.co.ke", password=PASSWORD, full_name="Ops Person",
        is_staff=True, staff_role=User.StaffRole.FOUNDER,
        email_verified_at=timezone.now(),
    )


@pytest.fixture
def org() -> Organisation:
    return Organisation.objects.create(name="Kilimani Dental")


@pytest.fixture
def client_user(org) -> User:
    user = User.objects.create_user(
        email="mercy@example.com", password=PASSWORD, full_name="Mercy",
        email_verified_at=timezone.now(),
    )
    Membership.objects.create(user=user, organisation=org)
    return user


def _ticket(org, client_user, subject="Cannot export last month"):
    return services.raise_ticket(
        organisation=org, actor=client_user, subject=subject,
        body="The export button returns an empty file for August.",
    )


# ── THE ONE THAT MATTERS ─────────────────────────────────────────────────────


def test_an_internal_note_never_reaches_the_client(client, org, staff, client_user):
    ticket = _ticket(org, client_user)

    services.reply_to_ticket(
        ticket=ticket, actor=staff,
        body="Their data is fine; the button is broken. Do not say that yet.",
        internal=True,
    )
    services.reply_to_ticket(
        ticket=ticket, actor=staff,
        body="We can reproduce this and are fixing it.",
    )

    client.force_login(client_user)
    body = client.get(reverse("support")).json()
    thread = body["tickets"][0]["messages"]

    texts = " ".join(m["body"] for m in thread)
    assert "We can reproduce this" in texts
    assert "Do not say that yet" not in texts
    assert "internal" not in str(thread), "the flag itself is not sent either"

    # And it does exist — the test would otherwise pass on a broken write.
    assert ticket.messages.filter(internal=True).count() == 1


def test_a_client_cannot_write_an_internal_note(client, org, client_user):
    """The flag must never be settable by the side it hides from."""
    ticket = _ticket(org, client_user)

    client.force_login(client_user)
    client.post(
        reverse("support-reply", args=[ticket.reference]),
        {"body": "Sneaky", "internal": True},
        content_type="application/json",
    )

    assert SupportMessage.objects.filter(internal=True).count() == 0


def test_staff_replying_internally_from_a_client_account_is_impossible(org, client_user):
    """Even at the service layer, `internal` requires is_staff."""
    ticket = _ticket(org, client_user)
    message = services.reply_to_ticket(
        ticket=ticket, actor=client_user, body="Anything", internal=True
    )
    assert message.internal is False


# ── isolation ────────────────────────────────────────────────────────────────


def test_one_client_cannot_read_or_reply_to_another_clients_ticket(
    client, org, client_user
):
    """A ticket reference is guessable — GM-SUP-2026-0001."""
    other = Organisation.objects.create(name="Someone Else")
    outsider = User.objects.create_user(
        email="outsider@example.com", password=PASSWORD,
        email_verified_at=timezone.now(),
    )
    Membership.objects.create(user=outsider, organisation=other)

    ticket = _ticket(org, client_user)

    client.force_login(outsider)
    assert client.get(reverse("support")).json()["tickets"] == []

    response = client.post(
        reverse("support-reply", args=[ticket.reference]),
        {"body": "Hello"}, content_type="application/json",
    )
    assert response.status_code == 404
    assert ticket.messages.count() == 1


# ── the promise we do not make ───────────────────────────────────────────────


def test_nothing_the_client_sees_promises_a_response_time(client, org, staff, client_user):
    """
    Charter 03 §IV — never a commitment that has not been tested. Priority and
    assignee are also absent: priority is our triage judgement, and showing it
    invites an argument about it rather than about the problem.
    """
    ticket = _ticket(org, client_user)
    services.set_ticket_state(
        ticket=ticket, actor=staff, priority=SupportTicket.Priority.URGENT,
        assigned_to=staff,
    )

    client.force_login(client_user)
    shown = client.get(reverse("support")).json()["tickets"][0]

    assert "priority" not in shown
    assert "assigned_to" not in shown
    assert "first_answered_at" not in shown

    blob = str(shown).lower()
    for phrase in ["within", "hours", "sla", "shortly", "soon", "guarantee"]:
        assert phrase not in blob, phrase


def test_the_wait_is_measured_even_though_it_is_not_promised(org, staff, client_user):
    ticket = _ticket(org, client_user)
    assert ticket.waited_for_first_answer() is None

    # An internal note is not an answer to anybody.
    services.reply_to_ticket(ticket=ticket, actor=staff, body="Looking.", internal=True)
    ticket.refresh_from_db()
    assert ticket.first_answered_at is None

    services.reply_to_ticket(ticket=ticket, actor=staff, body="We can reproduce it.")
    ticket.refresh_from_db()
    assert ticket.first_answered_at is not None
    assert ticket.waited_for_first_answer().total_seconds() >= 0

    # And it is the FIRST answer, not the most recent one.
    first = ticket.first_answered_at
    services.reply_to_ticket(ticket=ticket, actor=staff, body="Fixed.")
    ticket.refresh_from_db()
    assert ticket.first_answered_at == first


# ── the conversation ─────────────────────────────────────────────────────────


def test_a_client_replying_reopens_something_we_closed(org, staff, client_user):
    """
    Silently discarding that is how a client concludes nobody is listening.
    """
    ticket = _ticket(org, client_user)
    services.set_ticket_state(
        ticket=ticket, actor=staff, status=SupportTicket.Status.RESOLVED
    )
    assert ticket.resolved_at is not None

    services.reply_to_ticket(
        ticket=ticket, actor=client_user, body="It is happening again."
    )

    ticket.refresh_from_db()
    assert ticket.status == SupportTicket.Status.OPEN
    assert ticket.resolved_at is None


def test_raising_one_emails_us_with_the_question_in_it(org, client_user, settings):
    """
    An alert that only says a ticket exists forces somebody to sign in to find
    out whether it can wait — the decision the alert was meant to help with.
    """
    settings.SUPPORT_EMAIL = "edwin@genmars.co.ke"
    _ticket(org, client_user)

    message = mail.outbox[-1]
    assert message.to == ["edwin@genmars.co.ke"]
    assert "Kilimani Dental" in message.subject
    assert "empty file for August" in message.body


def test_a_reply_reaches_the_client_by_email_with_the_reply_in_it(org, staff, client_user):
    ticket = _ticket(org, client_user)
    services.reply_to_ticket(ticket=ticket, actor=staff, body="We can reproduce it.")

    message = mail.outbox[-1]
    assert message.to == [client_user.email]
    assert "We can reproduce it." in message.body


def test_an_internal_note_emails_nobody(org, staff, client_user):
    _ticket(org, client_user)
    before = len(mail.outbox)

    ticket = SupportTicket.objects.get()
    services.reply_to_ticket(
        ticket=ticket, actor=staff, body="Not for them.", internal=True
    )

    assert len(mail.outbox) == before


def test_mail_failing_never_loses_the_ticket(org, client_user, monkeypatch):
    """
    Mail is a convenience on top of the record, never the record itself. A
    provider having a bad afternoon must not lose a client's request.
    """
    from accounts import emails as email_module

    def boom(**kwargs):
        raise RuntimeError("provider down")

    monkeypatch.setattr(email_module, "send_support_raised", boom)

    ticket = _ticket(org, client_user)
    assert SupportTicket.objects.filter(pk=ticket.pk).exists()
    assert ticket.messages.count() == 1


def test_a_subject_alone_is_not_a_request(org, client_user):
    with pytest.raises(services.OperationsError) as caught:
        services.raise_ticket(
            organisation=org, actor=client_user, subject="Help", body="",
        )
    assert "not something anybody can act on" in str(caught.value)


def test_the_client_cannot_set_their_own_priority(client, org, client_user):
    """
    Every client-settable priority field ends up with everything marked urgent,
    which is the same as nothing being urgent.
    """
    client.force_login(client_user)
    client.post(
        reverse("support"),
        {"subject": "Everything is broken", "body": "Really.", "priority": "urgent"},
        content_type="application/json",
    )

    assert SupportTicket.objects.get().priority == SupportTicket.Priority.NORMAL
