"""
Change requests — Charter 05 §I, and the argument this model exists to prevent.

═══════════════════════════════════════════════════════════════════════════════
THE TESTS THAT MATTER MOST ARE THE THREE IN "what a client cannot be charged
for" AND test_raised_at_survives_every_later_edit.

The dispute is almost never about whether a change costs money. It is about
WHEN it was raised, and about a defect quietly becoming a billable change once
somebody knows how long it took to fix.
═══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from accounts.models import Membership, Organisation, User
from operations import services
from portal import selectors
from portal.models import ActivityLog, ChangeRequest, ContactLogEntry, Order

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
def spa() -> Organisation:
    return Organisation.objects.create(name="Clips Serenity Spa")


@pytest.fixture
def owner(spa) -> User:
    person = User.objects.create_user(
        email="owner@spa.co.ke", password=PASSWORD, full_name="The owner",
        email_verified_at=timezone.now(),
    )
    Membership.objects.create(user=person, organisation=spa)
    return person


@pytest.fixture
def order(spa, staff) -> Order:
    return Order.objects.create(
        organisation=spa, reference="GM-2026-0042", title="Spa website",
        contact=staff, scope="Five-page site with a booking enquiry form.",
        target_date=date(2026, 10, 1),
    )


@pytest.fixture
def change(order, staff) -> ChangeRequest:
    return services.raise_change_request(
        order=order, actor=staff, summary="Add a Google Ads manager view"
    )


# ── raising ──────────────────────────────────────────────────────────────────


def test_raising_needs_nothing_but_a_summary(order, staff):
    """
    Deliberately the cheapest thing in the module.

    Every field made mandatory here is a reason to leave it in WhatsApp and
    mean to record it later — and a request never written down is the failure
    the whole model exists to prevent.
    """
    change = services.raise_change_request(
        order=order, actor=staff, summary="Add a second payment provider"
    )
    assert change.status == ChangeRequest.Status.RAISED
    assert change.classification == ""
    assert change.reference.startswith("GM-CR-")


def test_a_client_can_raise_one_themselves(client, owner, order):
    """
    Charter 05 §I protects both sides. A change process only we can start lets
    a request be heard informally, absorbed, and disputed later with no record
    of when it was asked for.
    """
    client.force_login(owner)
    response = client.post(
        reverse("changes"),
        {"order": order.reference, "summary": "Move the booking form to the top"},
        content_type="application/json",
    )
    assert response.status_code == 201
    assert ChangeRequest.objects.get().raised_by == owner


def test_an_empty_summary_is_refused(order, staff):
    with pytest.raises(services.OperationsError):
        services.raise_change_request(order=order, actor=staff, summary="   ")


def test_a_closed_order_takes_no_change_requests(order, staff):
    """
    A request against finished work is new work. Letting it in here would
    reopen a closed engagement without anybody scoping or pricing it.
    """
    order.status = Order.Status.CLOSED
    order.save(update_fields=["status"])
    with pytest.raises(services.OperationsError) as exc:
        services.raise_change_request(order=order, actor=staff, summary="One more page")
    assert "enquiry" in str(exc.value).lower()


def test_a_conversation_from_another_client_is_refused(order, staff, spa):
    """
    The contact link is how a client's own words stay retrievable. Pointing it
    at another client's conversation would put their words on this record.
    """
    other = Organisation.objects.create(name="Someone else")
    theirs = ContactLogEntry.objects.create(
        organisation=other, recorded_by=staff, summary="Their message",
        channel=ContactLogEntry.Channel.WHATSAPP,
        direction=ContactLogEntry.Direction.INBOUND,
        happened_at=timezone.now(),
    )
    with pytest.raises(services.OperationsError):
        services.raise_change_request(
            order=order, actor=staff, summary="Something", contact=theirs
        )


def test_references_do_not_collide(order, staff):
    made = [
        services.raise_change_request(order=order, actor=staff, summary=f"Ask {n}")
        for n in range(4)
    ]
    assert len({c.reference for c in made}) == 4


# ── the timestamp the argument turns on ──────────────────────────────────────


def test_raised_at_survives_every_later_edit(change, staff):
    """
    ═══════════════════════════════════════════════════════════════════════════
    The single most important column in the model.

    Every other field can be revised as understanding improves. This one is the
    fact the dispute turns on, and a fact that can be adjusted afterwards is
    not evidence of anything.
    ═══════════════════════════════════════════════════════════════════════════
    """
    raised = change.raised_at

    services.classify_change_request(
        change=change, actor=staff,
        classification=ChangeRequest.Classification.CHANGE,
        note="New requirement, not in the signed scope.",
        cost_impact_kes=Decimal("12000"), timeline_impact_days=5,
    )
    change.refresh_from_db()
    assert change.raised_at == raised

    services.classify_change_request(
        change=change, actor=staff,
        classification=ChangeRequest.Classification.DEFECT,
        note="Re-read the scope; this was already promised.",
    )
    change.refresh_from_db()
    assert change.raised_at == raised


def test_time_spent_unclassified_is_measured(change, staff):
    """
    The number that says whether "classify before work starts" is a practice
    or a sentence in a document.
    """
    assert change.waited_to_be_classified() is None
    services.classify_change_request(
        change=change, actor=staff,
        classification=ChangeRequest.Classification.INCLUDED,
        note="Covered by the booking-form line in the scope.",
    )
    change.refresh_from_db()
    assert change.waited_to_be_classified() >= timedelta(0)


# ── what a client cannot be charged for ──────────────────────────────────────


@pytest.mark.parametrize(
    "classification",
    [
        ChangeRequest.Classification.INCLUDED,
        ChangeRequest.Classification.CLARIFICATION,
        ChangeRequest.Classification.DEFECT,
    ],
)
def test_only_a_change_request_can_carry_a_cost(change, staff, classification):
    """
    ═══════════════════════════════════════════════════════════════════════════
    A cost on a defect is a charge to fix what the client already paid for.
    A cost on a clarification is a charge for having explained something.

    Both are easy to do by accident on a form that shows the same fields for
    all four, which is exactly why the guard is in the service and not the UI.
    ═══════════════════════════════════════════════════════════════════════════
    """
    with pytest.raises(services.OperationsError) as exc:
        services.classify_change_request(
            change=change, actor=staff, classification=classification,
            note="A note.", cost_impact_kes=Decimal("5000"),
        )
    assert "change request" in str(exc.value).lower()


def test_only_a_change_request_can_move_the_date(change, staff):
    with pytest.raises(services.OperationsError):
        services.classify_change_request(
            change=change, actor=staff,
            classification=ChangeRequest.Classification.DEFECT,
            note="Our bug.", timeline_impact_days=4,
        )


def test_a_defect_never_becomes_billable(change, staff):
    services.classify_change_request(
        change=change, actor=staff,
        classification=ChangeRequest.Classification.DEFECT,
        note="The booking form does not send. Agreed scope, not working.",
    )
    change.refresh_from_db()
    assert change.bills is False
    assert change.cost_impact_kes is None


def test_reclassifying_away_from_change_clears_the_old_impact(change, staff):
    """
    A stale price is worse than none: it still reads as current, and it is the
    number a client would quote back.
    """
    services.classify_change_request(
        change=change, actor=staff,
        classification=ChangeRequest.Classification.CHANGE,
        note="New requirement.", cost_impact_kes=Decimal("12000"),
        timeline_impact_days=5, risk_note="Touches billing.",
    )
    services.classify_change_request(
        change=change, actor=staff,
        classification=ChangeRequest.Classification.INCLUDED,
        note="On a re-read this is covered by the reporting line.",
    )
    change.refresh_from_db()
    assert change.cost_impact_kes is None
    assert change.timeline_impact_days is None
    assert change.risk_note == ""
    assert change.status == ChangeRequest.Status.PROCEEDING


# ── the reasoning is the deliverable ─────────────────────────────────────────


@pytest.mark.parametrize("classification", ChangeRequest.Classification.values)
def test_every_classification_needs_a_reason(change, staff, classification):
    """
    Including "included in the agreed scope" — the one where a note feels most
    redundant and is most needed. That classification ends a commercial
    conversation before it starts, and an unexplained one is indistinguishable
    from not wanting to have the argument.
    """
    with pytest.raises(services.OperationsError) as exc:
        services.classify_change_request(
            change=change, actor=staff, classification=classification, note="  "
        )
    assert exc.value.field == "note"


def test_a_bogus_classification_is_refused(change, staff):
    with pytest.raises(services.OperationsError):
        services.classify_change_request(
            change=change, actor=staff, classification="probably-fine", note="Sure."
        )


def test_the_client_is_shown_the_reasoning(client, owner, change, staff):
    """
    A verdict without its reasoning is a bill with no explanation attached.
    Anything we would not show them does not belong in that field.
    """
    services.classify_change_request(
        change=change, actor=staff,
        classification=ChangeRequest.Classification.INCLUDED,
        note="Covered by the reporting line of the signed scope.",
    )
    client.force_login(owner)
    row = client.get(reverse("changes")).json()["changes"][0]
    assert "reporting line" in row["classification_note"]


# ── approval, and the date it moves ──────────────────────────────────────────


def test_a_change_waits_for_the_client(change, staff):
    services.classify_change_request(
        change=change, actor=staff,
        classification=ChangeRequest.Classification.CHANGE,
        note="New requirement.", cost_impact_kes=Decimal("12000"),
        timeline_impact_days=5,
    )
    change.refresh_from_db()
    assert change.status == ChangeRequest.Status.AWAITING
    assert change.needs_client is True


def test_the_other_three_do_not_wait_for_anybody(change, staff):
    """
    Asking a client to approve a defect fix is asking them to agree that we
    should do what they already paid for.
    """
    services.classify_change_request(
        change=change, actor=staff,
        classification=ChangeRequest.Classification.DEFECT,
        note="Agreed scope, not working.",
    )
    change.refresh_from_db()
    assert change.status == ChangeRequest.Status.PROCEEDING
    assert change.needs_client is False


def test_approval_moves_the_target_date_at_the_moment_it_is_agreed(
    client, owner, change, staff, order
):
    """
    ═══════════════════════════════════════════════════════════════════════════
    Charter 05 §II — the revised date is stated AT APPROVAL, not discovered
    later. This is the only moment when both sides are looking at the same
    number.
    ═══════════════════════════════════════════════════════════════════════════
    """
    services.classify_change_request(
        change=change, actor=staff,
        classification=ChangeRequest.Classification.CHANGE,
        note="New requirement.", cost_impact_kes=Decimal("12000"),
        timeline_impact_days=5,
    )
    client.force_login(owner)
    response = client.post(
        reverse("change-decision", args=[change.reference]),
        {"approved": True}, content_type="application/json",
    )
    assert response.status_code == 200
    order.refresh_from_db()
    assert order.target_date == date(2026, 10, 6)


def test_declining_leaves_the_date_alone(client, owner, change, staff, order):
    services.classify_change_request(
        change=change, actor=staff,
        classification=ChangeRequest.Classification.CHANGE,
        note="New requirement.", cost_impact_kes=Decimal("12000"),
        timeline_impact_days=5,
    )
    client.force_login(owner)
    client.post(
        reverse("change-decision", args=[change.reference]),
        {"approved": False, "note": "Not this quarter."},
        content_type="application/json",
    )
    order.refresh_from_db()
    assert order.target_date == date(2026, 10, 1)
    change.refresh_from_db()
    assert change.status == ChangeRequest.Status.DECLINED


def test_an_unpriced_change_cannot_be_answered_as_priced(change, staff):
    """
    Null and zero are different answers, and the sentence a client reads has to
    say which one they got.
    """
    services.classify_change_request(
        change=change, actor=staff,
        classification=ChangeRequest.Classification.CHANGE,
        note="New requirement, pricing to follow.",
    )
    change.refresh_from_db()
    assert "not yet priced" in services._impact_sentence(change)

    change.cost_impact_kes = Decimal("0")
    assert "no additional cost" in services._impact_sentence(change)


def test_a_decided_request_cannot_be_reclassified(client, owner, change, staff):
    """
    Re-classification is allowed while nothing has been decided, because a
    first read is often wrong. What it must not do is change the question
    after the client answered it.
    """
    services.classify_change_request(
        change=change, actor=staff,
        classification=ChangeRequest.Classification.CHANGE,
        note="New requirement.", cost_impact_kes=Decimal("12000"),
    )
    client.force_login(owner)
    client.post(
        reverse("change-decision", args=[change.reference]),
        {"approved": True}, content_type="application/json",
    )
    change.refresh_from_db()
    with pytest.raises(services.OperationsError) as exc:
        services.classify_change_request(
            change=change, actor=staff,
            classification=ChangeRequest.Classification.INCLUDED,
            note="Actually included.",
        )
    assert "already been answered" in str(exc.value)


def test_only_an_approved_change_bills(change, staff, client, owner):
    services.classify_change_request(
        change=change, actor=staff,
        classification=ChangeRequest.Classification.CHANGE,
        note="New requirement.", cost_impact_kes=Decimal("12000"),
    )
    change.refresh_from_db()
    assert change.bills is False

    client.force_login(owner)
    client.post(
        reverse("change-decision", args=[change.reference]),
        {"approved": True}, content_type="application/json",
    )
    change.refresh_from_db()
    assert change.bills is True


# ── closing ──────────────────────────────────────────────────────────────────


def test_an_unclassified_request_cannot_be_closed_as_done(change, staff):
    """
    ═══════════════════════════════════════════════════════════════════════════
    Closing it as done would record work that nobody decided was in scope,
    which is exactly how a fortnight goes missing — twenty small requests, each
    waved through on its own, and no moment at which anybody chose to absorb
    them.
    ═══════════════════════════════════════════════════════════════════════════
    """
    with pytest.raises(services.OperationsError) as exc:
        services.close_change_request(change=change, actor=staff)
    assert "classified" in str(exc.value).lower()


def test_an_unclassified_request_can_still_be_withdrawn(change, staff):
    """Nothing was built, so there is nothing to classify."""
    services.close_change_request(change=change, actor=staff, withdrawn=True)
    change.refresh_from_db()
    assert change.status == ChangeRequest.Status.WITHDRAWN


def test_a_change_awaiting_the_client_cannot_be_closed_as_done(change, staff):
    services.classify_change_request(
        change=change, actor=staff,
        classification=ChangeRequest.Classification.CHANGE,
        note="New requirement.", cost_impact_kes=Decimal("12000"),
    )
    change.refresh_from_db()
    with pytest.raises(services.OperationsError):
        services.close_change_request(change=change, actor=staff)


def test_a_proceeding_request_closes_cleanly(change, staff):
    services.classify_change_request(
        change=change, actor=staff,
        classification=ChangeRequest.Classification.DEFECT,
        note="Agreed scope, not working.",
    )
    change.refresh_from_db()
    services.close_change_request(change=change, actor=staff)
    change.refresh_from_db()
    assert change.status == ChangeRequest.Status.DONE
    assert change.is_open is False


# ── isolation ────────────────────────────────────────────────────────────────


def test_one_client_cannot_read_anothers_change_request(client, change, staff):
    """
    GM-CR-2026-0001 is as guessable as every other reference here, and a change
    request carries a client's own words plus a price.
    """
    other_org = Organisation.objects.create(name="Someone else")
    intruder = User.objects.create_user(
        email="them@elsewhere.co.ke", password=PASSWORD,
        email_verified_at=timezone.now(),
    )
    Membership.objects.create(user=intruder, organisation=other_org)

    client.force_login(intruder)
    assert client.get(reverse("changes")).json()["changes"] == []
    assert selectors.change_request_for(intruder, change.reference) is None


def test_a_stranger_cannot_answer_somebody_elses_change(client, change, staff):
    """404 rather than 403 — a refusal confirms the reference exists."""
    other_org = Organisation.objects.create(name="Someone else")
    intruder = User.objects.create_user(
        email="them2@elsewhere.co.ke", password=PASSWORD,
        email_verified_at=timezone.now(),
    )
    Membership.objects.create(user=intruder, organisation=other_org)
    services.classify_change_request(
        change=change, actor=staff,
        classification=ChangeRequest.Classification.CHANGE,
        note="New requirement.", cost_impact_kes=Decimal("12000"),
    )

    client.force_login(intruder)
    response = client.post(
        reverse("change-decision", args=[change.reference]),
        {"approved": True}, content_type="application/json",
    )
    assert response.status_code == 404
    change.refresh_from_db()
    assert change.status == ChangeRequest.Status.AWAITING


def test_a_client_cannot_raise_one_against_another_clients_order(
    client, order, spa
):
    other_org = Organisation.objects.create(name="Someone else")
    intruder = User.objects.create_user(
        email="them3@elsewhere.co.ke", password=PASSWORD,
        email_verified_at=timezone.now(),
    )
    Membership.objects.create(user=intruder, organisation=other_org)

    client.force_login(intruder)
    response = client.post(
        reverse("changes"),
        {"order": order.reference, "summary": "Add something"},
        content_type="application/json",
    )
    assert response.status_code == 404
    assert ChangeRequest.objects.count() == 0


# ── the client is told ───────────────────────────────────────────────────────


def test_classifying_raises_the_order_marker(change, staff, order):
    """
    The client asked a question about their scope and got an answer. That is
    precisely the class of change Charter 05 §I entitles them to notice.
    """
    services.classify_change_request(
        change=change, actor=staff,
        classification=ChangeRequest.Classification.INCLUDED,
        note="Covered by the scope.",
    )
    order.refresh_from_db()
    assert order.client_notice_at is not None
    assert change.reference in order.client_notice_reason


def test_every_step_is_logged(change, staff, client, owner):
    services.classify_change_request(
        change=change, actor=staff,
        classification=ChangeRequest.Classification.CHANGE,
        note="New requirement.", cost_impact_kes=Decimal("12000"),
    )
    client.force_login(owner)
    client.post(
        reverse("change-decision", args=[change.reference]),
        {"approved": True}, content_type="application/json",
    )
    actions = set(
        ActivityLog.objects.filter(subject=change.reference).values_list(
            "action", flat=True
        )
    )
    assert actions == {
        ActivityLog.Action.CHANGE_RAISED,
        ActivityLog.Action.CHANGE_CLASSIFIED,
        ActivityLog.Action.CHANGE_DECIDED,
    }


# ── the ops queue ────────────────────────────────────────────────────────────


def test_unclassified_requests_sort_to_the_top(client, order, staff):
    """
    ═══════════════════════════════════════════════════════════════════════════
    The ordering is the feature.

    An unclassified request is the only state with a cost attached to leaving
    it alone: work starts on it anyway, and by the time anybody classifies it
    the answer is contaminated by knowing how long it took. Newest-first would
    bury a four-day-old one under a batch of tidy new ones.
    ═══════════════════════════════════════════════════════════════════════════
    """
    stale = services.raise_change_request(
        order=order, actor=staff, summary="Asked on Monday"
    )
    fresh = services.raise_change_request(
        order=order, actor=staff, summary="Asked just now"
    )
    services.classify_change_request(
        change=fresh, actor=staff,
        classification=ChangeRequest.Classification.INCLUDED,
        note="Covered.",
    )

    client.force_login(staff)
    rows = client.get(reverse("ops-changes")).json()["changes"]
    assert rows[0]["reference"] == stale.reference


def test_staff_see_how_long_it_has_been_waiting(client, order, staff, change):
    client.force_login(staff)
    rows = client.get(reverse("ops-changes")).json()["changes"]
    assert rows[0]["waited_hours"] >= 0


def test_staff_can_classify_over_the_api(client, staff, change):
    client.force_login(staff)
    response = client.post(
        reverse("ops-change-classify", args=[change.reference]),
        {
            "classification": "change",
            "note": "New requirement, not in the signed scope.",
            "cost_impact_kes": "12000.00",
            "timeline_impact_days": 5,
        },
        content_type="application/json",
    )
    assert response.status_code == 200
    assert response.json()["status"] == ChangeRequest.Status.AWAITING


def test_the_api_refuses_a_cost_on_a_defect(client, staff, change):
    client.force_login(staff)
    response = client.post(
        reverse("ops-change-classify", args=[change.reference]),
        {"classification": "defect", "note": "Our bug.", "cost_impact_kes": "5000.00"},
        content_type="application/json",
    )
    assert response.status_code == 400
    assert "change request" in response.json()["detail"].lower()


def test_a_client_cannot_reach_the_ops_queue(client, owner, change):
    client.force_login(owner)
    assert client.get(reverse("ops-changes")).status_code == 403
