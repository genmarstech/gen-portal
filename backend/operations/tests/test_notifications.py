"""
Client notifications.

Charter 05 §III promises a written progress update every week. Until now the
client had to sign in to find out one existed, which is a promise kept in a
place nobody was told to look.

The rules here are about who does NOT get emailed, which is the half that goes
wrong quietly.
"""

from __future__ import annotations

import pytest
from django.core import mail
from django.urls import reverse
from django.utils import timezone

from accounts.models import Membership, Organisation, User
from operations import services
from portal.models import Enquiry, Order, ProgressNote

pytestmark = pytest.mark.django_db

PASSWORD = "correct-horse-battery"


@pytest.fixture
def staff() -> User:
    return User.objects.create_user(
        email="ops@genmars.co.ke", password=PASSWORD, full_name="Ops",
        is_staff=True, email_verified_at=timezone.now(),
    )


@pytest.fixture
def order(staff) -> Order:
    org = Organisation.objects.create(name="Kilimani Dental")
    client_user = User.objects.create_user(
        email="client@example.com", password=PASSWORD, full_name="A Client",
        email_verified_at=timezone.now(),
    )
    Membership.objects.create(user=client_user, organisation=org)
    enquiry = Enquiry.objects.create(
        organisation=org, submitted_by=client_user, problem="Reconciling by hand."
    )
    return services.convert_enquiry(
        enquiry=enquiry, actor=staff, title="Reconciliation tool", scope="Do the thing."
    )


def note_for(order: Order, staff: User) -> ProgressNote:
    return ProgressNote.objects.create(
        order=order, author=staff, week_of=timezone.localdate(),
        body="Import parser handles both statement formats.",
    )


# ── who gets it ──────────────────────────────────────────────────────────────


def test_publishing_emails_the_client(order, staff):
    mail.outbox.clear()
    services.publish_note(note=note_for(order, staff))

    assert len(mail.outbox) == 1
    sent = mail.outbox[0]
    assert sent.to == ["client@example.com"]
    assert order.reference in sent.subject
    # The note itself is in the email. "You have an update, sign in to read it"
    # is a notification about a notification.
    assert "Import parser handles both statement formats." in sent.body


def test_a_draft_emails_nobody(order, staff):
    """A draft is not a promise. Only publishing tells the client anything."""
    mail.outbox.clear()
    note_for(order, staff)
    assert mail.outbox == []


def test_publishing_twice_emails_once(order, staff):
    note = note_for(order, staff)
    services.publish_note(note=note)
    mail.outbox.clear()
    services.publish_note(note=note)
    assert mail.outbox == []


def test_someone_who_opted_out_is_not_emailed(order, staff):
    """
    A second or third person at a client often does not want every note, and
    service mail with no way to stop it becomes marketing in their mind.
    """
    quiet = User.objects.create_user(
        email="quiet@example.com", password=PASSWORD, email_verified_at=timezone.now()
    )
    Membership.objects.create(
        user=quiet, organisation=order.organisation, receives_updates=False
    )
    mail.outbox.clear()
    services.publish_note(note=note_for(order, staff))

    assert [m.to[0] for m in mail.outbox] == ["client@example.com"]


def test_an_unaccepted_invite_is_not_emailed_client_detail(order, staff):
    """
    An invited account that was never accepted is an address nobody has proved
    they read. Sending a client's commercial detail there sends it to whoever
    happens to own that mailbox.
    """
    invited, _ = services.invite_to_organisation(
        organisation=order.organisation, actor=staff, email="pending@example.com"
    )
    mail.outbox.clear()
    services.publish_note(note=note_for(order, staff))

    assert "pending@example.com" not in [m.to[0] for m in mail.outbox]


def test_another_organisation_is_never_emailed(order, staff):
    """The isolation rule, applied to the outbox rather than the database."""
    other_org = Organisation.objects.create(name="Someone Else Ltd")
    other = User.objects.create_user(
        email="other@example.com", password=PASSWORD, email_verified_at=timezone.now()
    )
    Membership.objects.create(user=other, organisation=other_org)
    mail.outbox.clear()
    services.publish_note(note=note_for(order, staff))

    assert "other@example.com" not in [m.to[0] for m in mail.outbox]


# ── failure ──────────────────────────────────────────────────────────────────


def test_a_mail_failure_does_not_un_publish_the_note(order, staff, monkeypatch):
    """
    The note is published the moment it is saved and the client can read it in
    the portal either way. Rolling the publish back because Resend was
    unreachable would let a mail outage silently retract a promise already made.
    """
    def boom(*args, **kwargs):
        raise RuntimeError("resend unreachable")

    monkeypatch.setattr("accounts.emails.send_progress_note", boom)
    note = services.publish_note(note=note_for(order, staff))

    note.refresh_from_db()
    assert note.is_published
    from portal.selectors import published_notes_for
    assert published_notes_for(order).count() == 1


def test_one_bad_address_does_not_stop_the_rest(order, staff, monkeypatch):
    second = User.objects.create_user(
        email="second@example.com", password=PASSWORD, email_verified_at=timezone.now()
    )
    Membership.objects.create(user=second, organisation=order.organisation)

    sent: list[str] = []

    def selective(email, **kwargs):
        if email == "client@example.com":
            raise RuntimeError("bad address")
        sent.append(email)

    monkeypatch.setattr("accounts.emails.send_progress_note", selective)
    services.publish_note(note=note_for(order, staff))

    assert sent == ["second@example.com"]


def test_the_email_makes_no_response_time_promise(order, staff):
    """
    Charter 03 §IV standing rule: never put a commitment in front of a client
    that has not been tested under real conditions. Tier 2 is not met.
    """
    mail.outbox.clear()
    services.publish_note(note=note_for(order, staff))
    body = mail.outbox[0].body.lower()
    for phrase in ["within 24", "within 48", "we will respond", "guarantee", "sla"]:
        assert phrase not in body
