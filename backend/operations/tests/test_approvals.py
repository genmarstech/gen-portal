"""
Asking a founder for permission to do one thing, once.

═══════════════════════════════════════════════════════════════════════════════
TWO TESTS HERE CARRY THE WHOLE FEATURE.

test_an_approval_is_spent_the_first_time_it_is_used — if an approval could be
used twice it is not an approval, it is a role change nobody decided to make.

test_a_role_change_cannot_be_requested — a request to become a founder is
exactly what an attacker with a delivery account would send. One distracted tap
and the permission model is gone, with an approval record making it look
deliberate.
═══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from accounts.models import Membership, Organisation, User
from operations import approvals, services
from portal.models import AccessRequest, ActivityLog, Offer

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
def engineer() -> User:
    return _staff("engineer@genmars.co.ke", User.StaffRole.DELIVERY)


@pytest.fixture
def spa() -> Organisation:
    return Organisation.objects.create(name="Clips Serenity Spa")


def _ask(client, action, subject, reason="The owner has closed the business."):
    return client.post(
        reverse("ops-requests"),
        {"action": action, "subject": subject, "reason": reason},
        content_type="application/json",
    )


def _answer(client, pk, decision, note=""):
    return client.post(
        reverse("ops-request", args=[pk]),
        {"decision": decision, "note": note},
        content_type="application/json",
    )


# ── the refusal now says what to do next ─────────────────────────────────────


def test_a_refusal_offers_a_way_forward(client, engineer, spa):
    """
    ── A 403 WITH NOWHERE TO GO IS WHY PEOPLE SHARE PASSWORDS ──────────────────

    The workflow used to leave the software here: a WhatsApp message, a call,
    and no record that any of it happened.
    """
    client.force_login(engineer)
    response = client.post(
        reverse("ops-client-admin", args=[spa.pk]),
        {"action": "archive"},
        content_type="application/json",
    )

    assert response.status_code == 403
    body = response.json()
    assert body["can_request"] is True
    assert body["action"] == "client.archive"
    assert body["subject"] == "Clips Serenity Spa"
    assert body["asking_for"] == "Archive a client"


def test_a_refusal_for_something_undelegable_offers_nothing(client, engineer):
    """
    No button, because there is no request to make. Offering one and refusing
    it later would be worse than not offering it.
    """
    other = _staff("other@genmars.co.ke", User.StaffRole.DELIVERY)
    client.force_login(engineer)
    response = client.patch(
        reverse("ops-staff-member", args=[other.pk]),
        {"role": "founder"},
        content_type="application/json",
    )
    assert response.status_code == 403
    assert "can_request" not in response.json()


# ── asking ───────────────────────────────────────────────────────────────────


def test_a_request_is_recorded_with_its_reason(client, engineer, spa):
    client.force_login(engineer)
    response = _ask(client, "client.archive", spa.name)

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "pending"
    assert body["requested_by"] == "engineer"
    assert body["what"]["label"] == "Archive a client"
    # The consequence, not a slug — it is what the decision actually turns on.
    assert "can be undone" in body["what"]["note"]


def test_a_request_without_a_reason_is_refused(client, engineer, spa):
    """Otherwise the founder is being asked to approve a verb."""
    client.force_login(engineer)
    response = _ask(client, "client.archive", spa.name, reason="  ")
    assert response.status_code == 400
    assert response.json()["field"] == "reason"


def test_a_role_change_cannot_be_requested(client, engineer):
    """
    ═══════════════════════════════════════════════════════════════════════════
    THE OMISSION THAT MATTERS MOST.

    Changing a role grants every other permission, including to itself.
    ═══════════════════════════════════════════════════════════════════════════
    """
    client.force_login(engineer)
    for forbidden in ("staff.role", "staff.invite", "billing.configure", "client.access"):
        response = _ask(client, forbidden, "anything")
        assert response.status_code == 400, forbidden
        assert not AccessRequest.objects.filter(action=forbidden).exists()


def test_the_undelegable_list_is_what_the_screen_is_told(client, engineer):
    client.force_login(engineer)
    delegable = {d["action"] for d in client.get(reverse("ops-requests")).json()["delegable"]}
    assert "client.archive" in delegable
    for forbidden in ("staff.role", "staff.invite", "billing.configure"):
        assert forbidden not in delegable


def test_asking_twice_does_not_queue_it_twice(client, engineer, spa):
    """Asking again is what somebody does when they have not heard back."""
    client.force_login(engineer)
    first = _ask(client, "client.archive", spa.name).json()["id"]
    second = _ask(client, "client.archive", spa.name).json()["id"]
    assert first == second
    assert AccessRequest.objects.count() == 1


# ── deciding ─────────────────────────────────────────────────────────────────


def test_only_a_founder_can_decide(client, engineer, spa):
    commercial = _staff("commercial@genmars.co.ke", User.StaffRole.COMMERCIAL)
    client.force_login(engineer)
    pk = _ask(client, "client.archive", spa.name).json()["id"]

    client.force_login(commercial)
    assert _answer(client, pk, "approve").status_code == 400
    assert AccessRequest.objects.get(pk=pk).is_open


def test_a_founder_cannot_approve_their_own_request(client, founder, spa):
    """
    An audit trail that reads like oversight and is not is worse than none.
    """
    client.force_login(founder)
    pk = _ask(client, "client.archive", spa.name).json()["id"]
    response = _answer(client, pk, "approve")
    assert response.status_code == 400
    assert "do it directly" in response.json()["detail"]


def test_approval_lets_the_requester_proceed(client, founder, engineer, spa):
    client.force_login(engineer)
    pk = _ask(client, "client.archive", spa.name).json()["id"]

    client.force_login(founder)
    assert _answer(client, pk, "approve", "Fine — she confirmed by phone.").status_code == 200

    client.force_login(engineer)
    response = client.post(
        reverse("ops-client-admin", args=[spa.pk]),
        {"action": "archive"},
        content_type="application/json",
    )
    assert response.status_code == 200
    spa.refresh_from_db()
    assert spa.is_archived


def test_an_approval_is_spent_the_first_time_it_is_used(client, founder, engineer, spa):
    """
    ═══════════════════════════════════════════════════════════════════════════
    THE GUARANTEE THE WHOLE FEATURE RESTS ON.

    An approval that could be used twice is a standing permission wearing the
    costume of a one-off.
    ═══════════════════════════════════════════════════════════════════════════
    """
    client.force_login(engineer)
    pk = _ask(client, "client.archive", spa.name).json()["id"]
    client.force_login(founder)
    _answer(client, pk, "approve")

    client.force_login(engineer)
    url = reverse("ops-client-admin", args=[spa.pk])
    assert client.post(url, {"action": "archive"}, content_type="application/json").status_code == 200

    entry = AccessRequest.objects.get(pk=pk)
    assert entry.used_at is not None
    assert entry.is_live() is False

    # Restore it as a founder, then try to archive again on the same approval.
    client.force_login(founder)
    client.post(url, {"action": "restore"}, content_type="application/json")

    client.force_login(engineer)
    second = client.post(url, {"action": "archive"}, content_type="application/json")
    assert second.status_code == 403

    spa.refresh_from_db()
    assert not spa.is_archived


def test_an_approval_cannot_be_spent_on_a_different_subject(client, founder, engineer, spa):
    """
    "Yes, archive Kilimani Dental" must not archive somebody else. This is why
    the check is in each view rather than in a decorator that cannot know
    which client the call is about.
    """
    other = Organisation.objects.create(name="Somebody Else Ltd")

    client.force_login(engineer)
    pk = _ask(client, "client.archive", spa.name).json()["id"]
    client.force_login(founder)
    _answer(client, pk, "approve")

    client.force_login(engineer)
    response = client.post(
        reverse("ops-client-admin", args=[other.pk]),
        {"action": "archive"},
        content_type="application/json",
    )
    assert response.status_code == 403
    other.refresh_from_db()
    assert not other.is_archived


def test_an_approval_cannot_be_spent_by_somebody_else(client, founder, engineer, spa):
    borrower = _staff("borrower@genmars.co.ke", User.StaffRole.DELIVERY)

    client.force_login(engineer)
    pk = _ask(client, "client.archive", spa.name).json()["id"]
    client.force_login(founder)
    _answer(client, pk, "approve")

    client.force_login(borrower)
    assert client.post(
        reverse("ops-client-admin", args=[spa.pk]),
        {"action": "archive"},
        content_type="application/json",
    ).status_code == 403


def test_an_expired_approval_is_dead(client, founder, engineer, spa):
    """
    An approval still valid next week is a permission nobody remembers
    granting.
    """
    client.force_login(engineer)
    pk = _ask(client, "client.archive", spa.name).json()["id"]
    client.force_login(founder)
    _answer(client, pk, "approve")

    entry = AccessRequest.objects.get(pk=pk)
    entry.expires_at = timezone.now() - timedelta(minutes=1)
    entry.save(update_fields=["expires_at"])

    client.force_login(engineer)
    assert client.post(
        reverse("ops-client-admin", args=[spa.pk]),
        {"action": "archive"},
        content_type="application/json",
    ).status_code == 403


def test_declining_leaves_them_where_they_were(client, founder, engineer, spa):
    client.force_login(engineer)
    pk = _ask(client, "client.archive", spa.name).json()["id"]

    client.force_login(founder)
    assert _answer(client, pk, "decline", "They still owe us for August.").status_code == 200

    client.force_login(engineer)
    assert client.post(
        reverse("ops-client-admin", args=[spa.pk]),
        {"action": "archive"},
        content_type="application/json",
    ).status_code == 403

    entry = AccessRequest.objects.get(pk=pk)
    assert entry.status == AccessRequest.Status.DECLINED
    assert entry.decision_note == "They still owe us for August."


def test_a_founder_can_do_it_themselves_instead(client, founder, engineer, spa):
    """
    "I did this for you" and "you may do this" are different decisions, and the
    log must not record the first as the second.
    """
    client.force_login(engineer)
    pk = _ask(client, "client.archive", spa.name).json()["id"]

    client.force_login(founder)
    assert _answer(client, pk, "do_it_myself", "Doing it now.").status_code == 200

    entry = AccessRequest.objects.get(pk=pk)
    assert entry.status == AccessRequest.Status.DONE_BY_FOUNDER
    assert entry.expires_at is None

    # No permission was lent, so the engineer still cannot.
    client.force_login(engineer)
    assert client.post(
        reverse("ops-client-admin", args=[spa.pk]),
        {"action": "archive"},
        content_type="application/json",
    ).status_code == 403


def test_a_decided_request_cannot_be_decided_again(client, founder, engineer, spa):
    client.force_login(engineer)
    pk = _ask(client, "client.archive", spa.name).json()["id"]
    client.force_login(founder)
    _answer(client, pk, "decline")
    assert _answer(client, pk, "approve").status_code == 400


def test_a_requester_can_withdraw_their_own(client, engineer, spa):
    client.force_login(engineer)
    pk = _ask(client, "client.archive", spa.name).json()["id"]
    assert _answer(client, pk, "withdraw").status_code == 200
    assert AccessRequest.objects.get(pk=pk).status == AccessRequest.Status.WITHDRAWN


def test_nobody_withdraws_somebody_elses(client, engineer, spa):
    other = _staff("other@genmars.co.ke", User.StaffRole.DELIVERY)
    client.force_login(engineer)
    pk = _ask(client, "client.archive", spa.name).json()["id"]

    client.force_login(other)
    assert _answer(client, pk, "withdraw").status_code == 400


# ── the same mechanism on a commercial act ───────────────────────────────────


def test_an_engineer_can_be_lent_the_send_of_one_quote(client, founder, engineer, spa):
    offer = services.make_offer(
        organisation=spa, actor=founder, title="Online booking",
        detail="Booking on the existing site.", amount_kes=Decimal("45000.00"),
        expires_on=timezone.localdate() + timedelta(days=21),
    )
    second = services.make_offer(
        organisation=spa, actor=founder, title="Something else",
        detail="Other work.", amount_kes=Decimal("9000.00"),
        expires_on=timezone.localdate() + timedelta(days=21),
    )

    client.force_login(engineer)
    refused = client.post(
        reverse("ops-offer-action", args=[offer.pk]),
        {"action": "send"}, content_type="application/json",
    )
    assert refused.status_code == 403
    assert refused.json()["subject"] == offer.reference

    pk = _ask(client, "offer.send", offer.reference, "She is waiting on it today.").json()["id"]
    client.force_login(founder)
    _answer(client, pk, "approve")

    client.force_login(engineer)
    assert client.post(
        reverse("ops-offer-action", args=[offer.pk]),
        {"action": "send"}, content_type="application/json",
    ).status_code == 200

    # And it bought exactly that one quote, not the other.
    assert client.post(
        reverse("ops-offer-action", args=[second.pk]),
        {"action": "send"}, content_type="application/json",
    ).status_code == 403
    second.refresh_from_db()
    assert second.status == Offer.Status.DRAFT


# ── the record ───────────────────────────────────────────────────────────────


def test_every_step_is_logged(client, founder, engineer, spa):
    client.force_login(engineer)
    pk = _ask(client, "client.archive", spa.name).json()["id"]
    client.force_login(founder)
    _answer(client, pk, "approve")
    client.force_login(engineer)
    client.post(
        reverse("ops-client-admin", args=[spa.pk]),
        {"action": "archive"}, content_type="application/json",
    )

    actions = set(ActivityLog.objects.values_list("action", flat=True))
    assert ActivityLog.Action.ACCESS_REQUESTED in actions
    assert ActivityLog.Action.ACCESS_APPROVED in actions
    # Asking, granting and SPENDING are three separate facts.
    assert ActivityLog.Action.ACCESS_USED in actions


def test_everyone_sees_the_queue(client, engineer, spa):
    """
    If three people are blocked on one person, that is not confidential — and a
    queue only the approver can see is one where nobody can tell whether asking
    even registered.
    """
    commercial = _staff("commercial@genmars.co.ke", User.StaffRole.COMMERCIAL)
    client.force_login(engineer)
    _ask(client, "client.archive", spa.name)

    client.force_login(commercial)
    body = client.get(reverse("ops-requests")).json()
    assert body["pending"] == 1
    assert body["may_decide"] is False
    assert body["requests"][0]["mine"] is False


def test_a_client_account_reaches_none_of_it(client, spa):
    outsider = User.objects.create_user(
        email="client@example.com", password=PASSWORD, email_verified_at=timezone.now()
    )
    Membership.objects.create(user=outsider, organisation=spa)
    client.force_login(outsider)
    assert client.get(reverse("ops-requests")).status_code == 403
    assert _ask(client, "client.archive", spa.name).status_code == 403
