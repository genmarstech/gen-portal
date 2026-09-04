"""
Quotes and proposals: the document, and getting it in front of somebody.

═══════════════════════════════════════════════════════════════════════════════
THE TEST THAT MATTERS IS test_sending_an_offer_emails_it.

Sending used to write a notification into the portal and stop, so a price sat
behind a login waiting for a client to happen to sign in. From our side that is
indistinguishable from having quoted somebody who went quiet — and the person
we talk to is usually not the person who signs off.
═══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from accounts.models import Membership, Organisation, User
from operations import services
from portal.models import Offer

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


@pytest.fixture
def owner(spa) -> User:
    person = User.objects.create_user(
        email="owner@spa.co.ke", password=PASSWORD, full_name="The owner",
        email_verified_at=timezone.now(),
    )
    Membership.objects.create(user=person, organisation=spa, receives_updates=True)
    return person


PROPOSAL = {
    "context": "Walk-ins are turned away because the paper diary is at reception.",
    "approach": "Online booking on the existing site, with SMS confirmation.",
    "inclusions": "Booking form, staff calendar, SMS on confirm.",
    "exclusions": "Card payments. Staff rostering.",
    "timeline": "Roughly three weeks once we have the branding.",
    "payment_terms": "Half on signature, half on acceptance.",
    "next_step": "Accept it here and we will send a statement of work to sign.",
}


def _draft(client, spa, **overrides):
    body = {
        "organisation": spa.pk,
        "title": "Online booking",
        "detail": "Booking on the existing site.",
        "amount_kes": "45000.00",
        "expires_on": str(timezone.localdate() + timedelta(days=21)),
        **PROPOSAL,
    }
    body.update(overrides)
    return client.post(reverse("ops-offers"), body, content_type="application/json")


# ── drafting a proposal ──────────────────────────────────────────────────────


def test_a_proposal_is_the_same_row_as_a_quote(client, founder, spa):
    """
    No second model. A renewal quoted in one line and a proposal somebody has
    to justify internally are one thing filled in to different depths.
    """
    client.force_login(founder)
    response = _draft(client, spa)

    assert response.status_code == 201
    body = response.json()
    assert body["context"].startswith("Walk-ins")
    assert body["exclusions"] == "Card payments. Staff rostering."
    assert body["status"] == "draft"


def test_a_one_line_quote_still_works(client, founder, spa):
    """Every proposal field is optional, and the document omits what is blank."""
    client.force_login(founder)
    response = _draft(client, spa, **{k: "" for k in PROPOSAL})
    assert response.status_code == 201
    assert response.json()["context"] == ""


def test_a_price_with_no_description_is_refused(client, founder, spa):
    """A number the client cannot evaluate and we cannot prove we described."""
    client.force_login(founder)
    response = _draft(client, spa, detail="", inclusions="")
    assert response.status_code == 400
    assert response.json()["field"] == "detail"


def test_the_structured_inclusions_satisfy_that_on_their_own(client, founder, spa):
    client.force_login(founder)
    assert _draft(
        client, spa, detail="", inclusions="Booking form, staff calendar."
    ).status_code == 201


# ── revising a draft ─────────────────────────────────────────────────────────


def test_a_draft_can_be_revised(client, founder, spa):
    client.force_login(founder)
    offer_id = _draft(client, spa).json()["id"]

    response = client.patch(
        reverse("ops-offer-action", args=[offer_id]),
        {"amount_kes": "52000.00", "timeline": "Four weeks."},
        content_type="application/json",
    )
    assert response.status_code == 200
    assert response.json()["amount_kes"] == "52000.00"
    assert response.json()["timeline"] == "Four weeks."


def test_a_sent_offer_cannot_be_edited(client, founder, spa, owner):
    """
    ── THE TWO-STEP SEND IS THE WHOLE REASON ───────────────────────────────────

    Once sent, the client can accept it, so the number and the words are ours
    to honour. Editing them under somebody still deciding means they open on
    Friday something different from what they read on Tuesday.
    """
    client.force_login(founder)
    offer_id = _draft(client, spa).json()["id"]
    client.post(
        reverse("ops-offer-action", args=[offer_id]),
        {"action": "send"}, content_type="application/json",
    )

    response = client.patch(
        reverse("ops-offer-action", args=[offer_id]),
        {"amount_kes": "10.00"},
        content_type="application/json",
    )
    assert response.status_code == 400
    assert "withdraw it and make a new one" in response.json()["detail"]
    assert Offer.objects.get(pk=offer_id).amount_kes == Decimal("45000.00")


# ── sending it ───────────────────────────────────────────────────────────────


def test_sending_an_offer_emails_it(client, founder, spa, owner, mailoutbox):
    """
    ═══════════════════════════════════════════════════════════════════════════
    THE GAP THIS FEATURE CLOSED.

    A quote that only appears in a portal is a quote the person who signs off
    never sees.
    ═══════════════════════════════════════════════════════════════════════════
    """
    client.force_login(founder)
    offer_id = _draft(client, spa).json()["id"]

    assert client.post(
        reverse("ops-offer-action", args=[offer_id]),
        {"action": "send"}, content_type="application/json",
    ).status_code == 200

    assert len(mailoutbox) == 1
    message = mailoutbox[0]
    assert "Online booking" in message.subject
    assert Offer.objects.get(pk=offer_id).reference in message.subject


def test_the_whole_quote_is_in_the_message_not_behind_a_link(client, founder, spa, owner, mailoutbox):
    """
    Same rule as a progress note: "you have a quote, sign in to read it" is a
    notification about a notification. It also has to survive being forwarded.
    """
    client.force_login(founder)
    offer_id = _draft(client, spa).json()["id"]
    client.post(
        reverse("ops-offer-action", args=[offer_id]),
        {"action": "send"}, content_type="application/json",
    )

    body = mailoutbox[0].body
    assert "Walk-ins are turned away" in body
    assert "Card payments" in body
    assert "45,000.00" in body
    assert "Half on signature" in body


def test_the_expiry_is_stated_once_and_not_used_as_pressure(client, founder, spa, owner, mailoutbox):
    """
    Charter 04 §III — specific over impressive. The date exists because an
    open-ended price is one we are still bound by after our costs have moved,
    not to hurry anybody.
    """
    client.force_login(founder)
    offer_id = _draft(client, spa).json()["id"]
    client.post(
        reverse("ops-offer-action", args=[offer_id]),
        {"action": "send"}, content_type="application/json",
    )

    body = mailoutbox[0].body.lower()
    assert body.count("valid until") == 1
    for pressure in ("act now", "don't miss", "limited time", "hurry", "last chance"):
        assert pressure not in body


def test_a_discount_shows_what_it_is_a_discount_from(client, founder, spa, owner, mailoutbox):
    """
    A price without its reference point is a number they cannot judge, and
    hiding it would make the discount a sales tactic rather than a fact.
    """
    offer = services.make_offer(
        organisation=spa, actor=founder, title="Online booking",
        detail="Booking on the existing site.", amount_kes=Decimal("45000.00"),
        expires_on=timezone.localdate() + timedelta(days=21),
    )
    offer.list_price_kes = Decimal("60000.00")
    offer.save(update_fields=["list_price_kes"])

    services.send_offer(offer=offer, actor=founder)
    assert "60,000.00" in mailoutbox[0].body


def test_nobody_is_emailed_who_asked_not_to_be_or_never_verified(client, founder, spa, mailoutbox):
    quiet = User.objects.create_user(
        email="quiet@spa.co.ke", password=PASSWORD, email_verified_at=timezone.now()
    )
    Membership.objects.create(user=quiet, organisation=spa, receives_updates=False)
    ghost = User.objects.create_user(email="ghost@spa.co.ke", password=PASSWORD)
    Membership.objects.create(user=ghost, organisation=spa, receives_updates=True)

    client.force_login(founder)
    offer_id = _draft(client, spa).json()["id"]
    client.post(
        reverse("ops-offer-action", args=[offer_id]),
        {"action": "send"}, content_type="application/json",
    )
    assert mailoutbox == []


def test_the_recipient_name_is_frozen_at_send(client, founder, spa, owner):
    """
    Same rule as an invoice. Renaming the client must not change the "To:" line
    on a quote they are holding a printed copy of.
    """
    client.force_login(founder)
    offer_id = _draft(client, spa).json()["id"]
    client.post(
        reverse("ops-offer-action", args=[offer_id]),
        {"action": "send"}, content_type="application/json",
    )

    client.patch(
        reverse("ops-client-admin", args=[spa.pk]),
        {"name": "Serenity Spa & Wellness"},
        content_type="application/json",
    )

    client.logout()
    client.force_login(owner)
    reference = Offer.objects.get(pk=offer_id).reference
    document = client.get(reverse("offer-document", args=[reference])).json()
    assert document["offered_to"]["organisation"] == "Clips Serenity Spa"


# ── the document ─────────────────────────────────────────────────────────────


def test_the_client_can_open_the_quote_as_a_document(client, founder, spa, owner):
    client.force_login(founder)
    offer_id = _draft(client, spa).json()["id"]
    client.post(
        reverse("ops-offer-action", args=[offer_id]),
        {"action": "send"}, content_type="application/json",
    )
    reference = Offer.objects.get(pk=offer_id).reference

    client.logout()
    client.force_login(owner)
    response = client.get(reverse("offer-document", args=[reference]))

    assert response.status_code == 200
    body = response.json()
    assert body["offer"]["reference"] == reference
    assert body["proposal"]["context"].startswith("Walk-ins")
    assert body["terms"]["payment_terms"] == "Half on signature, half on acceptance."
    # Who is quoting — same source as an invoice, so the two cannot disagree
    # about who Genmars is.
    assert "legal_name" in body["biller"]


def test_headings_nobody_filled_in_are_absent_not_empty(client, founder, spa, owner):
    """A document with five blank headings reads as unfinished."""
    client.force_login(founder)
    offer_id = _draft(client, spa, **{k: "" for k in PROPOSAL}).json()["id"]
    client.post(
        reverse("ops-offer-action", args=[offer_id]),
        {"action": "send"}, content_type="application/json",
    )
    reference = Offer.objects.get(pk=offer_id).reference

    client.logout()
    client.force_login(owner)
    body = client.get(reverse("offer-document", args=[reference])).json()

    assert body["proposal"] == {}
    assert body["terms"]["payment_terms"] is None


def test_another_clients_quote_is_a_404(client, founder, spa, owner):
    """
    Not a 403. Offer references are sequential and guessable, so a 403 would
    confirm which are real — a counter of how much Genmars is quoting, and to
    whom.
    """
    other = Organisation.objects.create(name="Somebody Else Ltd")
    client.force_login(founder)
    offer_id = _draft(client, other).json()["id"]
    client.post(
        reverse("ops-offer-action", args=[offer_id]),
        {"action": "send"}, content_type="application/json",
    )
    reference = Offer.objects.get(pk=offer_id).reference

    client.logout()
    client.force_login(owner)
    assert client.get(reverse("offer-document", args=[reference])).status_code == 404


def test_a_draft_cannot_be_opened_by_guessing_its_reference(client, founder, spa, owner):
    """
    An offer we have not sent is not one they have received. Showing it would
    put a price in front of somebody before we had decided to.
    """
    client.force_login(founder)
    offer_id = _draft(client, spa).json()["id"]
    reference = Offer.objects.get(pk=offer_id).reference

    client.logout()
    client.force_login(owner)
    assert client.get(reverse("offer-document", args=[reference])).status_code == 404


def test_an_anonymous_request_gets_nothing(client, founder, spa):
    client.force_login(founder)
    offer_id = _draft(client, spa).json()["id"]
    reference = Offer.objects.get(pk=offer_id).reference
    client.logout()
    assert client.get(reverse("offer-document", args=[reference])).status_code in (401, 403)


def test_quoting_is_a_commercial_decision(client, spa):
    """Charter 02 §I — an offer is a price the client can accept."""
    engineer = _staff("dev@genmars.co.ke", User.StaffRole.DELIVERY)
    client.force_login(engineer)
    assert _draft(client, spa).status_code == 403
    assert not Offer.objects.exists()
