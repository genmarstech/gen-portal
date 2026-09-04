"""
Work that happened before it was written down, ongoing work, and the task board.

═══════════════════════════════════════════════════════════════════════════════
THE TEST THAT MATTERS IS test_backfilling_past_work_does_not_raise_ten_alarms.

Charter 05 §III promises a written note every week, and the operations queue
counts active orders that have not had one. Backfilling a year of delivered
work would light that counter up for engagements that finished long ago —
notes that were never going to be written, for a promise not in force at the
time. A founder who sees ten false alarms stops reading the real one.
═══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from accounts.models import Membership, Organisation, User
from operations import services
from portal.models import Decision, Order, SupportTicket, Task

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


def _open(client, spa, **overrides):
    body = {
        "title": "Spa website",
        "scope": "Five-page site with a booking enquiry form.",
        "exclusions": "Online payments.",
    }
    body.update(overrides)
    return client.post(
        reverse("ops-client-orders", args=[spa.pk]), body, content_type="application/json"
    )


# ── recording work that already happened ─────────────────────────────────────


def test_past_work_can_be_recorded_with_the_dates_it_actually_ran(client, founder, spa):
    client.force_login(founder)
    response = _open(
        client, spa,
        started_on="2025-03-01",
        completed_on="2025-05-20",
    )
    assert response.status_code == 201

    order = Order.objects.get(reference=response.json()["reference"])
    assert order.started_on == date(2025, 3, 1)
    assert order.completed_on == date(2025, 5, 20)
    # Delivered, not scoping. An order recorded as scoping for something
    # delivered last year sits on the delivery board forever.
    assert order.status == Order.Status.DELIVERED
    assert order.recorded_retrospectively is True


def test_backfilling_past_work_does_not_raise_ten_alarms(client, founder, spa):
    """
    ═══════════════════════════════════════════════════════════════════════════
    A founder who sees ten false alarms stops reading the real one.
    ═══════════════════════════════════════════════════════════════════════════
    """
    client.force_login(founder)
    for n in range(3):
        _open(
            client, spa, title=f"Past work {n}",
            started_on="2025-01-10", completed_on="2025-02-10",
        )

    counts = client.get(reverse("ops-overview")).json()["counts"]
    assert counts["awaiting_note"] == 0


def test_an_ongoing_retainer_recorded_from_a_past_start_still_counts_as_history(
    client, founder, spa
):
    """
    Started last year, still running. It is retrospective — the notes for the
    months before it was entered here were never going to be written.
    """
    client.force_login(founder)
    response = _open(
        client, spa, title="Monthly upkeep", kind="retainer",
        started_on="2025-06-01", status="active",
    )
    assert response.status_code == 201

    order = Order.objects.get(reference=response.json()["reference"])
    assert order.kind == Order.Kind.RETAINER
    assert order.status == Order.Status.ACTIVE
    assert order.recorded_retrospectively is True


def test_past_work_never_emails_the_client(client, founder, spa, owner, mailoutbox):
    """
    "This is what we understood you asked for, nothing has started yet" is a
    lie about something delivered a year ago, and it would arrive looking like
    we had lost track of what we had already done for them.
    """
    client.force_login(founder)
    _open(client, spa, started_on="2025-03-01", completed_on="2025-05-20")
    assert mailoutbox == []


def test_new_work_opened_today_still_emails(client, founder, spa, owner, mailoutbox):
    """The guard above must not have switched the ordinary path off."""
    client.force_login(founder)
    _open(client, spa)
    assert len(mailoutbox) == 1


def test_work_cannot_have_finished_before_it_started(client, founder, spa):
    client.force_login(founder)
    response = _open(client, spa, started_on="2025-05-01", completed_on="2025-03-01")
    assert response.status_code == 400
    assert response.json()["field"] == "completed_on"


def test_a_completion_date_cannot_be_in_the_future(client, founder, spa):
    client.force_login(founder)
    ahead = (timezone.localdate() + timedelta(days=30)).isoformat()
    response = _open(client, spa, completed_on=ahead)
    assert response.status_code == 400
    assert response.json()["field"] == "completed_on"


# ── the shape of the work ────────────────────────────────────────────────────


@pytest.mark.parametrize("kind", ["project", "retainer", "updates", "hosting"])
def test_the_four_shapes_of_work(client, founder, spa, kind):
    client.force_login(founder)
    response = _open(client, spa, kind=kind)
    assert response.status_code == 201
    assert Order.objects.get(reference=response.json()["reference"]).kind == kind


def test_a_retainer_gets_no_delivery_gates(client, founder, spa):
    """
    Gates describe a project being built. A retainer is never "done", and six
    unmet gates against one is a delivery board describing something that is
    not happening.
    """
    client.force_login(founder)
    reference = _open(client, spa, kind="retainer").json()["reference"]
    assert Order.objects.get(reference=reference).gates.count() == 0


def test_a_new_project_still_gets_its_gates(client, founder, spa):
    client.force_login(founder)
    reference = _open(client, spa).json()["reference"]
    assert Order.objects.get(reference=reference).gates.count() > 0


def test_past_work_gets_no_gates_either(client, founder, spa):
    """It has already been built. Gates would be six things nobody will meet."""
    client.force_login(founder)
    reference = _open(
        client, spa, started_on="2025-03-01", completed_on="2025-05-20"
    ).json()["reference"]
    assert Order.objects.get(reference=reference).gates.count() == 0


def test_an_unknown_kind_is_refused(client, founder, spa):
    client.force_login(founder)
    response = _open(client, spa, kind="something-else")
    assert response.status_code == 400
    assert response.json()["field"] == "kind"


# ── what the client sees ─────────────────────────────────────────────────────


def test_the_client_sees_the_shape_and_the_dates(client, founder, spa, owner):
    client.force_login(founder)
    reference = _open(
        client, spa, title="Monthly upkeep", kind="retainer", started_on="2025-06-01",
    ).json()["reference"]

    client.logout()
    client.force_login(owner)
    body = client.get(reverse("order-detail", args=[reference])).json()

    assert body["kind"] == "retainer"
    assert body["kind_label"] == "Retainer"
    assert body["started_on"] == "2025-06-01"
    # So the page can stop promising a weekly note against work recorded after
    # the fact.
    assert body["recorded_retrospectively"] is True


def test_a_client_can_ask_for_a_change_against_one_order(client, founder, spa, owner):
    """
    The order page has always TOLD them that anything outside the scope is a
    change request. This is the route that lets them make one, filed against
    the work it is about rather than as a loose support request.
    """
    client.force_login(founder)
    reference = _open(client, spa).json()["reference"]

    client.logout()
    client.force_login(owner)
    response = client.post(
        reverse("support"),
        {
            "subject": "Add online payments",
            "body": "We would like card payments on the booking form.",
            "order": reference,
        },
        content_type="application/json",
    )
    assert response.status_code == 201

    ticket = SupportTicket.objects.get()
    assert ticket.order.reference == reference
    assert ticket.organisation == spa


def test_a_change_request_cannot_be_filed_against_another_clients_order(
    client, founder, spa, owner
):
    """
    The order is looked up through the client's own memberships, so somebody
    else's reference matches nothing and the ticket is filed loose rather than
    against work they cannot see.
    """
    other = Organisation.objects.create(name="Somebody Else Ltd")
    client.force_login(founder)
    theirs = _open(client, other).json()["reference"]

    client.logout()
    client.force_login(owner)
    response = client.post(
        reverse("support"),
        {"subject": "Change", "body": "Something.", "order": theirs},
        content_type="application/json",
    )
    assert response.status_code == 201
    assert SupportTicket.objects.get().order is None


# ── the task board ───────────────────────────────────────────────────────────


def test_a_task_can_hang_off_a_client_a_ticket_or_a_decision(client, founder, spa):
    client.force_login(founder)
    decision = services.record_decision(
        actor=founder, title="Bill in KES only",
        context="Two enquiries asked for USD.", decision="Every invoice is KES.",
    )
    ticket = services.raise_ticket(
        organisation=spa, actor=founder, subject="Site is slow",
        body="Pages take ten seconds.",
    )

    for payload, field in (
        ({"organisation": spa.pk}, "organisation_name"),
        ({"ticket": ticket.reference}, "ticket_reference"),
        ({"decision": decision.pk}, "decision_reference"),
    ):
        response = client.post(
            reverse("ops-tasks"),
            {"assignee": founder.pk, "title": f"Work about {field}", **payload},
            content_type="application/json",
        )
        assert response.status_code == 201, response.json()
        assert response.json()[field] is not None


def test_the_client_is_inferred_from_whatever_the_task_is_about(client, founder, spa):
    """
    So that "what is outstanding for this client" finds work filed against
    their order or their ticket, without anybody having set it twice.
    """
    client.force_login(founder)
    reference = _open(client, spa).json()["reference"]

    response = client.post(
        reverse("ops-tasks"),
        {"assignee": founder.pk, "title": "Chase the logo files", "order": reference},
        content_type="application/json",
    )
    assert response.status_code == 201
    assert Task.objects.get().organisation == spa


def test_a_task_cannot_point_at_two_different_clients(client, founder, spa):
    """A row that appears under both and is right about neither."""
    other = Organisation.objects.create(name="Somebody Else Ltd")
    client.force_login(founder)
    reference = _open(client, spa).json()["reference"]

    response = client.post(
        reverse("ops-tasks"),
        {
            "assignee": founder.pk, "title": "Confused task",
            "order": reference, "organisation": other.pk,
        },
        content_type="application/json",
    )
    assert response.status_code == 400
    assert response.json()["field"] == "order"


def test_the_board_can_be_filtered_to_one_client(client, founder, spa):
    other = Organisation.objects.create(name="Somebody Else Ltd")
    client.force_login(founder)
    for org, title in ((spa, "Theirs"), (other, "Somebody else's")):
        client.post(
            reverse("ops-tasks"),
            {"assignee": founder.pk, "title": title, "organisation": org.pk},
            content_type="application/json",
        )

    tasks = client.get(reverse("ops-tasks"), {"organisation": spa.pk}).json()["tasks"]
    assert [t["title"] for t in tasks] == ["Theirs"]


def test_a_task_with_nothing_attached_is_still_allowed(client, founder):
    """
    A board that demands to know which project a task belongs to is a board
    people stop using — a real day contains "chase the KRA PIN" too.
    """
    client.force_login(founder)
    response = client.post(
        reverse("ops-tasks"),
        {"assignee": founder.pk, "title": "Chase the KRA PIN"},
        content_type="application/json",
    )
    assert response.status_code == 201
    assert Task.objects.get().organisation is None


# ── who may put work on whose board ──────────────────────────────────────────


def test_anybody_can_pick_up_work_themselves(client, spa):
    """
    Writing down what you are going to do next is not a management act, and
    needing permission for it would mean the board stops describing what people
    are actually working on.
    """
    engineer = _staff("engineer@genmars.co.ke", User.StaffRole.DELIVERY)
    client.force_login(engineer)

    response = client.post(
        reverse("ops-tasks"),
        {"assignee": engineer.pk, "title": "Fix the slow query"},
        content_type="application/json",
    )
    assert response.status_code == 201


def test_nobody_puts_work_on_somebody_elses_board(client, founder):
    """
    ═══════════════════════════════════════════════════════════════════════════
    ASSIGNING WORK TO SOMEBODY ELSE IS DIRECTING THEIR TIME.

    This was open to every staff account, which treated assignment as a filing
    action when it is a management one — anyone could put a task with a due
    date on anyone else's board, and the person it landed on had no say.
    ═══════════════════════════════════════════════════════════════════════════
    """
    engineer = _staff("engineer@genmars.co.ke", User.StaffRole.DELIVERY)
    commercial = _staff("commercial@genmars.co.ke", User.StaffRole.COMMERCIAL)

    for actor in (engineer, commercial):
        client.force_login(actor)
        response = client.post(
            reverse("ops-tasks"),
            {"assignee": founder.pk, "title": "Do this by Friday"},
            content_type="application/json",
        )
        assert response.status_code == 403, actor.email
        # And the refusal offers a way forward rather than being a dead end.
        assert response.json()["can_request"] is True

    assert not Task.objects.exists()


def test_a_founder_assigns_freely(client, founder):
    engineer = _staff("engineer@genmars.co.ke", User.StaffRole.DELIVERY)
    client.force_login(founder)
    response = client.post(
        reverse("ops-tasks"),
        {"assignee": engineer.pk, "title": "Fix the slow query"},
        content_type="application/json",
    )
    assert response.status_code == 201


def test_assigning_to_somebody_else_can_be_asked_for(client, founder):
    """
    So somebody who genuinely needs to hand work over can ask, rather than
    sending a message that leaves no record.
    """
    engineer = _staff("engineer@genmars.co.ke", User.StaffRole.DELIVERY)
    colleague = _staff("colleague@genmars.co.ke", User.StaffRole.DELIVERY)

    client.force_login(engineer)
    asked = client.post(
        reverse("ops-requests"),
        {
            "action": "task.assign",
            "subject": colleague.full_name,
            "reason": "She is the only one who has touched that integration.",
        },
        content_type="application/json",
    ).json()

    client.force_login(founder)
    client.post(
        reverse("ops-request", args=[asked["id"]]),
        {"decision": "approve"},
        content_type="application/json",
    )

    client.force_login(engineer)
    response = client.post(
        reverse("ops-tasks"),
        {"assignee": colleague.pk, "title": "Take over the Daraja callback"},
        content_type="application/json",
    )
    assert response.status_code == 201

    # And it bought one assignment, not the role.
    again = client.post(
        reverse("ops-tasks"),
        {"assignee": colleague.pk, "title": "And this too"},
        content_type="application/json",
    )
    assert again.status_code == 403
