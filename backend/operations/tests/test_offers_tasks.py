"""
Offers and tasks.

An offer is a commitment: once sent, the client can accept it and the number is
ours to honour. The tests here are mostly about what that forbids.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from accounts.models import Membership, Organisation, User
from operations import services
from portal.models import ActivityLog, Enquiry, Notification, Offer, Task

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


def _offer(org, staff, **kwargs):
    return services.make_offer(
        organisation=org, actor=staff,
        title=kwargs.pop("title", "Reconciliation tool, Business Setup"),
        detail=kwargs.pop("detail", "Discovery, configuration, migration, training."),
        amount_kes=kwargs.pop("amount_kes", Decimal("60000.00")),
        expires_on=kwargs.pop("expires_on", timezone.localdate() + timedelta(days=14)),
        **kwargs,
    )


# ── an offer is a commitment ─────────────────────────────────────────────────


def test_an_offer_starts_as_a_draft(org, staff):
    """
    Drafting is cheap and editable; sending is a commitment. One click on a
    form with a typo in the amount should not bind us.
    """
    offer = _offer(org, staff)
    assert offer.status == Offer.Status.DRAFT
    assert offer.sent_at is None
    assert not Notification.objects.filter(kind=Notification.Kind.OFFER_SENT).exists()


def test_sending_freezes_it_and_tells_the_client(org, staff, client_user):
    offer = _offer(org, staff)
    services.send_offer(offer=offer, actor=staff)

    offer.refresh_from_db()
    assert offer.status == Offer.Status.SENT
    assert offer.sent_at is not None

    note = Notification.objects.get(kind=Notification.Kind.OFFER_SENT)
    assert note.user == client_user
    assert note.audience == Notification.Audience.CLIENT


def test_an_offer_needs_an_expiry_that_has_not_passed(org, staff):
    with pytest.raises(services.OperationsError) as caught:
        _offer(org, staff, expires_on=None)
    assert "still bound by in a year" in str(caught.value)

    with pytest.raises(services.OperationsError):
        _offer(org, staff, expires_on=timezone.localdate() - timedelta(days=1))


def test_an_expired_offer_cannot_be_accepted(org, staff, client_user):
    offer = _offer(org, staff)
    services.send_offer(offer=offer, actor=staff)
    Offer.objects.filter(pk=offer.pk).update(
        expires_on=timezone.localdate() - timedelta(days=1)
    )
    offer.refresh_from_db()

    with pytest.raises(services.OperationsError) as caught:
        services.accept_offer(offer=offer, actor=client_user)

    # And the refusal offers the way forward rather than just saying no.
    assert "ask us for a fresh one" in str(caught.value).lower()


def test_accepting_files_an_enquiry_and_does_not_start_work(org, staff, client_user):
    """
    Charter 02 §I puts a signed statement of work before delivery. No click by
    a client may skip that.
    """
    offer = _offer(org, staff)
    services.send_offer(offer=offer, actor=staff)
    services.accept_offer(offer=offer, actor=client_user)

    offer.refresh_from_db()
    assert offer.status == Offer.Status.ACCEPTED
    assert offer.enquiry is not None
    assert offer.enquiry.status == Enquiry.Status.NEW

    from portal.models import Order
    assert not Order.objects.exists(), "accepting must not create an order"


def test_an_accepted_offer_cannot_be_withdrawn(org, staff, client_user):
    """The client acted on it. Undoing that unilaterally is a conversation."""
    offer = _offer(org, staff)
    services.send_offer(offer=offer, actor=staff)
    services.accept_offer(offer=offer, actor=client_user)

    with pytest.raises(services.OperationsError) as caught:
        services.withdraw_offer(offer=offer, actor=staff, reason="Changed our mind.")
    assert "conversation with the client" in str(caught.value)


def test_a_discount_is_visible_against_the_list_price(org, staff):
    from portal.models import Service, ServiceTier

    service = Service.objects.create(name="Implementation", slug="implementation", summary="s")
    tier = ServiceTier.objects.create(
        service=service, slug="business-setup", name="Business Setup",
        price_kes=Decimal("75000.00"), lead="l", includes="i",
    )

    offer = _offer(org, staff, amount_kes=Decimal("60000.00"), tier=tier)

    assert offer.list_price_kes == Decimal("75000.00")
    assert offer.discount_kes == Decimal("15000.00")
    assert offer.tier_name == "Business Setup"


def test_sending_is_written_to_the_log_with_both_prices(org, staff):
    from portal.models import Service, ServiceTier

    service = Service.objects.create(name="Implementation", slug="implementation", summary="s")
    tier = ServiceTier.objects.create(
        service=service, slug="business-setup", name="Business Setup",
        price_kes=Decimal("75000.00"), lead="l", includes="i",
    )
    offer = _offer(org, staff, amount_kes=Decimal("60000.00"), tier=tier)
    services.send_offer(offer=offer, actor=staff)

    entry = ActivityLog.objects.get(action=ActivityLog.Action.OFFER_SENT)
    assert entry.detail["amount_kes"] == "60000.00"
    assert entry.detail["list_price_kes"] == "75000.00"


# ── tasks ────────────────────────────────────────────────────────────────────


def test_a_task_goes_to_one_person_and_only_they_are_told(staff):
    """A team-wide notification for one person's task teaches people to ignore them."""
    colleague = User.objects.create_user(
        email="asha@genmars.co.ke", password=PASSWORD, full_name="Asha",
        is_staff=True, staff_role=User.StaffRole.COMMERCIAL,
        email_verified_at=timezone.now(),
    )

    services.assign_task(actor=staff, assignee=colleague, title="Draft the SOW")

    notes = Notification.objects.filter(kind=Notification.Kind.TASK_ASSIGNED)
    assert notes.count() == 1
    assert notes.first().user == colleague


def test_work_cannot_be_assigned_to_a_revoked_account(staff):
    """It would never move, and its owner would never see it."""
    gone = User.objects.create_user(
        email="left@genmars.co.ke", password=PASSWORD, full_name="Left",
        is_staff=True, staff_role=User.StaffRole.DELIVERY, is_active=False,
    )

    with pytest.raises(services.OperationsError) as caught:
        services.assign_task(actor=staff, assignee=gone, title="Anything")
    assert "never see this" in str(caught.value)


def test_work_cannot_be_assigned_to_a_client(staff, client_user):
    with pytest.raises(services.OperationsError) as caught:
        services.assign_task(actor=staff, assignee=client_user, title="Anything")
    assert "internal work" in str(caught.value)


def test_blocked_needs_a_reason(staff):
    task = services.assign_task(actor=staff, assignee=staff, title="Do the thing")

    with pytest.raises(services.OperationsError) as caught:
        services.set_task_status(task=task, actor=staff, status=Task.Status.BLOCKED)
    assert "Blocked on what" in str(caught.value)

    services.set_task_status(
        task=task, actor=staff, status=Task.Status.BLOCKED,
        blocked_reason="Waiting on the client's data export.",
    )
    task.refresh_from_db()
    assert task.status == Task.Status.BLOCKED


def test_unblocking_clears_the_reason(staff):
    task = services.assign_task(actor=staff, assignee=staff, title="Do the thing")
    services.set_task_status(
        task=task, actor=staff, status=Task.Status.BLOCKED,
        blocked_reason="Waiting on data.",
    )
    services.set_task_status(task=task, actor=staff, status=Task.Status.DOING)

    task.refresh_from_db()
    assert task.blocked_reason == "", "a stale reason describes a block that is over"


def test_completing_is_logged_and_timestamped(staff):
    task = services.assign_task(actor=staff, assignee=staff, title="Do the thing")
    services.set_task_status(task=task, actor=staff, status=Task.Status.DONE)

    task.refresh_from_db()
    assert task.done_at is not None
    assert ActivityLog.objects.filter(action=ActivityLog.Action.TASK_DONE).exists()


def test_a_task_needs_no_order(staff):
    """Chasing a supplier, fixing the backup script — real work, no order."""
    task = services.assign_task(actor=staff, assignee=staff, title="Fix the backup script")
    assert task.order_id is None


# ── what the client can reach ────────────────────────────────────────────────


def test_a_client_never_sees_a_draft(client, org, staff, client_user):
    """
    An offer we have not sent is not one they have received. Showing it puts a
    price in front of somebody before we decided to.
    """
    _offer(org, staff)

    client.force_login(client_user)
    body = client.get(reverse("offer-list")).json()
    assert body["offers"] == []


def test_a_client_sees_their_sent_offers_and_what_was_discounted(
    client, org, staff, client_user
):
    from portal.models import Service, ServiceTier

    service = Service.objects.create(name="Implementation", slug="implementation", summary="s")
    tier = ServiceTier.objects.create(
        service=service, slug="business-setup", name="Business Setup",
        price_kes=Decimal("75000.00"), lead="l", includes="i",
    )
    offer = _offer(org, staff, amount_kes=Decimal("60000.00"), tier=tier)
    services.send_offer(offer=offer, actor=staff)

    client.force_login(client_user)
    body = client.get(reverse("offer-list")).json()

    assert len(body["offers"]) == 1
    shown = body["offers"][0]
    assert shown["amount_kes"] == "60000.00"
    # Shown deliberately: a price with no reference point is one they cannot
    # judge, and hiding it makes the discount a tactic rather than a fact.
    assert shown["list_price_kes"] == "75000.00"
    assert shown["discount_kes"] == "15000.00"


def test_one_client_cannot_reach_another_clients_offer(client, org, staff):
    """The reference is guessable — GM-OFR-2026-0001."""
    other = Organisation.objects.create(name="Someone Else")
    outsider = User.objects.create_user(
        email="outsider@example.com", password=PASSWORD,
        email_verified_at=timezone.now(),
    )
    Membership.objects.create(user=outsider, organisation=other)

    offer = _offer(org, staff)
    services.send_offer(offer=offer, actor=staff)

    client.force_login(outsider)
    assert client.get(reverse("offer-list")).json()["offers"] == []

    response = client.post(
        reverse("offer-decision", args=[offer.reference]),
        {"decision": "accept"}, content_type="application/json",
    )
    assert response.status_code == 404

    offer.refresh_from_db()
    assert offer.status == Offer.Status.SENT


def test_a_client_accepts_over_http(client, org, staff, client_user):
    offer = _offer(org, staff)
    services.send_offer(offer=offer, actor=staff)

    client.force_login(client_user)
    response = client.post(
        reverse("offer-decision", args=[offer.reference]),
        {"decision": "accept"}, content_type="application/json",
    )

    assert response.status_code == 200, response.content
    assert response.json()["status"] == "accepted"

    offer.refresh_from_db()
    assert offer.accepted_by == client_user
    assert offer.enquiry is not None
