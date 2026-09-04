"""
The client record: who they are, what we run for them, and what was said.

═══════════════════════════════════════════════════════════════════════════════
TWO TESTS HERE MATTER MORE THAN THE REST.

test_the_contact_log_is_never_client_visible — this log is written honestly
because nobody outside Genmars reads it. If a client-facing endpoint ever
serves one of these rows, the log stops being written honestly the same week.

test_naming_a_client_publicly_demands_the_evidence — Charter 04 §V. A tick with
nothing behind it is how somebody ends up on a public page having never agreed
to it.
═══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from accounts.models import Membership, Organisation, User
from operations import selectors, services
from portal.models import ActivityLog, ContactLogEntry, HostingArrangement, Order

pytestmark = pytest.mark.django_db

PASSWORD = "correct-horse-battery"


@pytest.fixture
def staff() -> User:
    return User.objects.create_user(
        email="ops@genmars.co.ke",
        password=PASSWORD,
        full_name="Ops Person",
        is_staff=True,
        staff_role=User.StaffRole.FOUNDER,
        email_verified_at=timezone.now(),
    )


@pytest.fixture
def spa() -> Organisation:
    return Organisation.objects.create(name="Clips Serenity Spa")


@pytest.fixture
def other() -> Organisation:
    return Organisation.objects.create(name="Somebody Else Ltd")


# ── the client's own details ─────────────────────────────────────────────────


def test_a_client_page_works_before_anything_has_been_filled_in(client, staff, spa):
    """
    The commonest state, and it must be an ordinary empty page rather than a
    500. The profile is created on first read for exactly this reason.
    """
    client.force_login(staff)
    response = client.get(reverse("ops-client", args=[spa.pk]))

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Clips Serenity Spa"
    assert body["profile"]["contact_name"] == ""
    assert body["hosting"] == []
    assert body["contact_log"] == []


def test_client_details_are_saved_and_logged(client, staff, spa):
    client.force_login(staff)
    response = client.patch(
        reverse("ops-client", args=[spa.pk]),
        {
            "what_they_do": "A day spa in Kilimani",
            "contact_name": "The owner",
            "contact_phone": "+254700000000",
            "preferred_channel": "whatsapp",
        },
        content_type="application/json",
    )
    assert response.status_code == 200
    assert response.json()["channel_label"] == "WhatsApp"

    entry = ActivityLog.objects.filter(
        action=ActivityLog.Action.CLIENT_PROFILE_CHANGED
    ).first()
    assert entry is not None
    assert entry.organisation_id == spa.id
    # Field NAMES, not values — same rule as the billing profile.
    assert "contact_phone" in entry.detail["fields"]
    assert "+254700000000" not in entry.summary


def test_naming_a_client_publicly_demands_the_evidence(client, staff, spa):
    """
    Charter 04 §V — written permission, or we do not name them. The refusal
    names the field so the form can point at it.
    """
    client.force_login(staff)
    refused = client.patch(
        reverse("ops-client", args=[spa.pk]),
        {"may_be_named": True},
        content_type="application/json",
    )
    assert refused.status_code == 400
    assert refused.json()["field"] == "permission_note"

    assert selectors.client_profile(spa).may_be_named is False


def test_permission_with_its_evidence_is_recorded_as_its_own_line(client, staff, spa):
    client.force_login(staff)
    response = client.patch(
        reverse("ops-client", args=[spa.pk]),
        {
            "may_be_named": True,
            "permission_note": "Email from the owner, 2026-09-04, filed in 07-executed/",
        },
        content_type="application/json",
    )
    assert response.status_code == 200
    assert selectors.client_profile(spa).may_be_named is True

    # Two entries: the field change, and the fact itself in words. The second
    # is what somebody reads when asking why a client is on a public page.
    summaries = [
        e.summary
        for e in ActivityLog.objects.filter(
            action=ActivityLog.Action.CLIENT_PROFILE_CHANGED
        )
    ]
    assert any("may now be named publicly" in s for s in summaries)


def test_a_no_op_save_records_nothing(client, staff, spa):
    client.force_login(staff)
    body = {"contact_name": "The owner"}
    client.patch(reverse("ops-client", args=[spa.pk]), body, content_type="application/json")
    before = ActivityLog.objects.count()
    client.patch(reverse("ops-client", args=[spa.pk]), body, content_type="application/json")
    assert ActivityLog.objects.count() == before


# ── hosting and renewals ─────────────────────────────────────────────────────


def test_a_hosting_arrangement_is_recorded_with_who_holds_the_account(client, staff, spa):
    """
    Charter 05 §VIII. Whose name a domain is in decides whether the client can
    leave with it, and that has to be a visible decision rather than a default.
    """
    client.force_login(staff)
    response = client.post(
        reverse("ops-client-hosting", args=[spa.pk]),
        {
            "kind": "domain",
            "identifier": "clipsserenityspa.co.ke",
            "provider": "Truehost",
            "account_holder": "client",
            "renews_on": str(timezone.localdate() + timedelta(days=20)),
        },
        content_type="application/json",
    )
    assert response.status_code == 201
    assert response.json()["holder_label"] == "The client"
    assert response.json()["days_until_renewal"] == 20


def test_hosting_needs_something_to_identify_it(client, staff, spa):
    client.force_login(staff)
    response = client.post(
        reverse("ops-client-hosting", args=[spa.pk]),
        {"kind": "domain", "identifier": "   "},
        content_type="application/json",
    )
    assert response.status_code == 400
    assert response.json()["field"] == "identifier"


def test_renewals_due_includes_things_that_have_already_lapsed(staff, spa):
    """
    An expired domain is not less urgent than one expiring Friday — it is the
    emergency. A forward-only window would drop it on the morning it mattered.
    """
    today = timezone.localdate()
    HostingArrangement.objects.create(
        organisation=spa, kind="domain", identifier="lapsed.co.ke",
        renews_on=today - timedelta(days=3),
    )
    HostingArrangement.objects.create(
        organisation=spa, kind="domain", identifier="soon.co.ke",
        renews_on=today + timedelta(days=10),
    )
    HostingArrangement.objects.create(
        organisation=spa, kind="domain", identifier="later.co.ke",
        renews_on=today + timedelta(days=200),
    )

    due = [h.identifier for h in selectors.renewals_due(within_days=30)]
    assert due == ["lapsed.co.ke", "soon.co.ke"]


def test_a_retired_arrangement_stops_counting_but_is_not_deleted(client, staff, spa):
    """
    The row stays. Whether we once held a client's domain is the one question
    worth being able to answer years later.
    """
    client.force_login(staff)
    arrangement = HostingArrangement.objects.create(
        organisation=spa, kind="domain", identifier="old.co.ke",
        renews_on=timezone.localdate() + timedelta(days=5),
    )

    response = client.delete(
        reverse("ops-hosting", args=[arrangement.pk]),
        {"reason": "Client moved to their own registrar"},
        content_type="application/json",
    )
    assert response.status_code == 200

    arrangement.refresh_from_db()
    assert arrangement.retired_at is not None
    assert HostingArrangement.objects.filter(pk=arrangement.pk).exists()
    assert selectors.renewals_due(within_days=30).count() == 0
    assert arrangement.days_until_renewal() is None


def test_retiring_twice_is_refused(client, staff, spa):
    client.force_login(staff)
    arrangement = HostingArrangement.objects.create(
        organisation=spa, kind="domain", identifier="old.co.ke"
    )
    client.delete(reverse("ops-hosting", args=[arrangement.pk]), content_type="application/json")
    again = client.delete(
        reverse("ops-hosting", args=[arrangement.pk]), content_type="application/json"
    )
    assert again.status_code == 400


def test_renewals_reach_the_queue_the_whole_company_looks_at(client, staff, spa):
    """A date nobody is looking at is the failure mode."""
    HostingArrangement.objects.create(
        organisation=spa, kind="domain", identifier="clipsserenityspa.co.ke",
        renews_on=timezone.localdate() + timedelta(days=14),
    )
    client.force_login(staff)
    counts = client.get(reverse("ops-overview")).json()["counts"]
    assert counts["renewals_due"] == 1


# ── the contact log ──────────────────────────────────────────────────────────


def _log(client, org, **overrides):
    body = {
        "channel": "whatsapp",
        "direction": "inbound",
        "summary": "Wants online booking added to the site",
        "detail": "Owner messaged on Saturday. Walk-ins are being turned away.",
        "with_whom": "The owner",
    }
    body.update(overrides)
    return client.post(
        reverse("ops-client-contact", args=[org.pk]), body, content_type="application/json"
    )


def test_a_conversation_is_recorded_with_who_wrote_it_down(client, staff, spa):
    client.force_login(staff)
    response = _log(client, spa)

    assert response.status_code == 201
    body = response.json()
    assert body["channel_label"] == "WhatsApp"
    assert body["direction_label"] == "They contacted us"
    assert body["recorded_by"] == "Ops Person"
    assert body["is_owed"] is False


def test_a_conversation_needs_a_line_saying_what_it_was_about(client, staff, spa):
    client.force_login(staff)
    response = _log(client, spa, summary="   ")
    assert response.status_code == 400
    assert response.json()["field"] == "summary"


def test_a_follow_up_without_a_date_is_refused(client, staff, spa):
    """
    An undated follow-up never reaches a queue and never goes overdue, so it
    behaves exactly like not writing it down — while feeling like you did.
    """
    client.force_login(staff)
    response = _log(client, spa, follow_up="Send a quote for the booking feature")
    assert response.status_code == 400
    assert response.json()["field"] == "follow_up_by"
    assert not ContactLogEntry.objects.exists()


def test_a_dated_follow_up_becomes_something_owed(client, staff, spa):
    client.force_login(staff)
    response = _log(
        client,
        spa,
        follow_up="Send a quote for the booking feature",
        follow_up_by=str(timezone.localdate() + timedelta(days=2)),
    )
    assert response.status_code == 201
    assert response.json()["is_owed"] is True
    assert response.json()["is_overdue"] is False


def test_an_overdue_follow_up_reaches_the_queue(client, staff, spa):
    client.force_login(staff)
    _log(
        client,
        spa,
        follow_up="Send a quote",
        follow_up_by=str(timezone.localdate() - timedelta(days=1)),
    )
    counts = client.get(reverse("ops-overview")).json()["counts"]
    assert counts["follow_ups_due"] == 1


def test_clearing_a_follow_up_does_not_rewrite_what_was_promised(client, staff, spa):
    """
    A log that edited the promise when it was kept could never answer whether
    what we did was what we said.
    """
    client.force_login(staff)
    entry_id = _log(
        client,
        spa,
        follow_up="Send a quote for the booking feature",
        follow_up_by=str(timezone.localdate()),
    ).json()["id"]

    response = client.patch(
        reverse("ops-follow-ups"),
        {"id": entry_id, "note": "Quoted KES 45,000"},
        content_type="application/json",
    )
    assert response.status_code == 200

    entry = ContactLogEntry.objects.get(pk=entry_id)
    assert entry.cleared_at is not None
    assert entry.cleared_by == staff
    assert entry.follow_up == "Send a quote for the booking feature"
    assert entry.is_owed is False


def test_clearing_a_follow_up_twice_is_refused(client, staff, spa):
    client.force_login(staff)
    entry_id = _log(
        client, spa, follow_up="Send a quote", follow_up_by=str(timezone.localdate())
    ).json()["id"]
    url = reverse("ops-follow-ups")
    client.patch(url, {"id": entry_id}, content_type="application/json")
    again = client.patch(url, {"id": entry_id}, content_type="application/json")
    assert again.status_code == 400


def test_an_entry_with_nothing_owed_cannot_be_cleared(client, staff, spa):
    client.force_login(staff)
    entry_id = _log(client, spa).json()["id"]
    response = client.patch(
        reverse("ops-follow-ups"), {"id": entry_id}, content_type="application/json"
    )
    assert response.status_code == 400


def test_a_conversation_cannot_be_filed_under_another_clients_order(client, staff, spa, other):
    """Would put one client's project into another client's history."""
    theirs = Order.objects.create(
        organisation=other, reference="GM-2026-0099", title="Their project", contact=staff
    )
    client.force_login(staff)
    response = _log(client, spa, order=theirs.reference)
    assert response.status_code == 400
    assert response.json()["field"] == "order"


def test_a_conversation_cannot_be_dated_in_the_future(client, staff, spa):
    client.force_login(staff)
    response = _log(
        client, spa, happened_at=(timezone.now() + timedelta(days=1)).isoformat()
    )
    assert response.status_code == 400
    assert response.json()["field"] == "happened_at"


def test_a_conversation_can_be_written_up_the_next_morning(client, staff, spa):
    """
    happened_at is editable and not auto_now_add. A log that stamped everything
    with the moment somebody found time to type it would put Friday's call on
    Monday and make the order of events wrong.
    """
    when = timezone.now() - timedelta(days=3)
    client.force_login(staff)
    response = _log(client, spa, happened_at=when.isoformat())

    assert response.status_code == 201
    entry = ContactLogEntry.objects.get(pk=response.json()["id"])
    assert abs((entry.happened_at - when).total_seconds()) < 2


def test_the_contact_log_is_never_client_visible(client, staff, spa):
    """
    ═══════════════════════════════════════════════════════════════════════════
    THE TEST THAT MATTERS IN THIS FILE.

    This log is written honestly because nobody outside Genmars reads it. If a
    client-facing endpoint ever serves one of these rows, it stops being
    written honestly the same week — and the export is the endpoint most likely
    to grow one by accident, because it exists to hand over everything.
    ═══════════════════════════════════════════════════════════════════════════
    """
    client.force_login(staff)
    _log(client, spa, detail="Owner sounded fed up with the old booking system.")
    client.logout()

    member = User.objects.create_user(
        email="owner@clipsserenityspa.co.ke",
        password=PASSWORD,
        email_verified_at=timezone.now(),
    )
    Membership.objects.create(user=member, organisation=spa)
    client.force_login(member)

    # Charter 05 §VIII hands a client everything we hold about them. This is
    # the one internal record, and it must not be in there.
    export = client.get(reverse("export")).content.decode()
    assert "fed up" not in export
    assert "old booking system" not in export

    # And the client record itself is staff-only.
    assert client.get(reverse("ops-client", args=[spa.pk])).status_code == 403
    assert client.get(reverse("ops-follow-ups")).status_code == 403


def test_the_client_page_shows_the_conversation_and_the_work_together(client, staff, spa):
    order = Order.objects.create(
        organisation=spa, reference="GM-2026-0042", title="Spa website", contact=staff
    )
    client.force_login(staff)
    _log(client, spa, order=order.reference)

    body = client.get(reverse("ops-client", args=[spa.pk])).json()
    assert body["contact_log"][0]["order_reference"] == "GM-2026-0042"
    assert [o["reference"] for o in body["orders"]] == ["GM-2026-0042"]


def test_follow_ups_are_oldest_first_across_every_client(client, staff, spa, other):
    """
    Ascending, like the enquiry queue. The one waiting longest is the one most
    at risk of being forgotten.
    """
    client.force_login(staff)
    _log(client, other, follow_up="Second", follow_up_by=str(date(2026, 9, 10)))
    _log(client, spa, follow_up="First", follow_up_by=str(date(2026, 9, 1)))

    owed = client.get(reverse("ops-follow-ups")).json()["follow_ups"]
    assert [f["follow_up"] for f in owed] == ["First", "Second"]


def test_an_unknown_client_is_a_404(client, staff):
    client.force_login(staff)
    assert client.get(reverse("ops-client", args=[99999])).status_code == 404
    assert (
        client.post(
            reverse("ops-client-contact", args=[99999]),
            {"channel": "call", "direction": "inbound", "summary": "x"},
            content_type="application/json",
        ).status_code
        == 404
    )


# ── a conversation becoming work ─────────────────────────────────────────────


def test_a_promise_on_a_call_lands_on_the_board(client, staff, spa):
    """
    "I'll send you a quote Thursday" is not a record of a chat, it is an
    obligation with a deadline — and the commonest thing this company drops.
    """
    from portal.models import Task

    client.force_login(staff)
    due = str(timezone.localdate() + timedelta(days=2))
    _log(client, spa, follow_up="Send a quote for the booking feature", follow_up_by=due)

    task = Task.objects.get()
    assert task.title == "Send a quote for the booking feature"
    assert task.due_on == timezone.localdate() + timedelta(days=2)
    assert task.organisation == spa
    # Assigned to whoever had the conversation — they are the only person who
    # could act on it today, and assigning to yourself needs no permission.
    assert task.assignee == staff
    assert task.priority == Task.Priority.HIGH


def test_a_conversation_about_a_specific_order_becomes_work(client, staff, spa):
    """
    Talking about an order almost always means something changed about it, and
    the caller is the only person who knows what.
    """
    from portal.models import Task

    order = Order.objects.create(
        organisation=spa, reference="GM-2026-0042", title="Spa website", contact=staff
    )
    client.force_login(staff)
    _log(client, spa, order=order.reference)

    task = Task.objects.get()
    assert task.order == order
    assert task.title.startswith("Follow up:")
    # Provenance, so "why am I doing this" is answerable six weeks later.
    assert task.contact is not None
    assert task.priority == Task.Priority.NORMAL


def test_an_ordinary_conversation_creates_no_task(client, staff, spa):
    """
    ═══════════════════════════════════════════════════════════════════════════
    THE RESTRAINT IS THE DESIGN.

    Making every logged message a task fails within a fortnight: the board
    fills with "called about the invoice" rows nobody will ever tick off, and
    a board that is mostly noise is worse than no board — the noise is
    indistinguishable from work at a glance.
    ═══════════════════════════════════════════════════════════════════════════
    """
    from portal.models import Task

    client.force_login(staff)
    _log(client, spa)
    assert not Task.objects.exists()


def test_the_task_can_be_declined_at_the_point_of_logging(client, staff, spa):
    from portal.models import Task

    order = Order.objects.create(
        organisation=spa, reference="GM-2026-0042", title="Spa website", contact=staff
    )
    client.force_login(staff)
    _log(client, spa, order=order.reference, create_task=False)
    assert not Task.objects.exists()


def test_clearing_the_follow_up_closes_its_task(client, staff, spa):
    """
    Otherwise the same thing is marked finished in two places, and the second
    is the one people forget — so the board fills with work completed weeks ago,
    which is how a board stops being believed.
    """
    from portal.models import Task

    client.force_login(staff)
    entry_id = _log(
        client, spa, follow_up="Send a quote", follow_up_by=str(timezone.localdate())
    ).json()["id"]

    client.patch(
        reverse("ops-follow-ups"), {"id": entry_id}, content_type="application/json"
    )

    task = Task.objects.get()
    assert task.status == Task.Status.DONE
    assert task.done_at is not None


def test_work_from_a_call_is_visible_to_the_whole_company(client, staff, spa):
    """
    The board only lived on the team screen, so a task raised from a call was
    visible to whoever went looking rather than to everybody. It is counted on
    the queue now.
    """
    client.force_login(staff)
    _log(
        client, spa, follow_up="Send a quote",
        follow_up_by=str(timezone.localdate() + timedelta(days=1)),
    )

    counts = client.get(reverse("ops-overview")).json()["counts"]
    assert counts["open_tasks"] == 1


# ── assigning work off a conversation ────────────────────────────────────────


def test_the_picker_offers_conversations_nobody_has_picked_up(client, staff, spa):
    """
    One that already became a task has been acted on. Left in the list it is an
    invitation to create a second task for the same call — and on the board the
    duplicate is indistinguishable from the original.
    """
    order = Order.objects.create(
        organisation=spa, reference="GM-2026-0042", title="Spa website", contact=staff
    )
    client.force_login(staff)

    # This one auto-creates a task, so it should not be offered.
    _log(client, spa, summary="Talked about the booking form", order=order.reference)
    # This one does not.
    _log(client, spa, summary="Asked how long hosting is paid up for")

    offered = client.get(reverse("ops-conversations")).json()["conversations"]
    assert [c["summary"] for c in offered] == ["Asked how long hosting is paid up for"]

    everything = client.get(reverse("ops-conversations"), {"all": "1"}).json()
    assert len(everything["conversations"]) == 2


def test_assigning_off_a_conversation_carries_the_client_and_the_order(client, staff, spa):
    """
    So nobody retypes a reference that is already recorded against the call.
    """
    from portal.models import Task

    order = Order.objects.create(
        organisation=spa, reference="GM-2026-0042", title="Spa website", contact=staff
    )
    client.force_login(staff)
    entry_id = _log(
        client, spa, summary="Wants the gallery reordered",
        order=order.reference, create_task=False,
    ).json()["id"]

    response = client.post(
        reverse("ops-tasks"),
        {
            "assignee": staff.pk,
            "title": "Reorder the gallery",
            "contact": entry_id,
        },
        content_type="application/json",
    )
    assert response.status_code == 201

    task = Task.objects.get()
    assert task.contact_id == entry_id
    assert task.organisation == spa
    assert task.order == order


def test_a_conversation_from_another_client_is_refused(client, staff, spa):
    """A task pointing at one client's conversation and another's order would
    appear under both and be right about neither."""
    other = Organisation.objects.create(name="Somebody Else Ltd")
    client.force_login(staff)
    entry_id = _log(client, other, create_task=False).json()["id"]

    response = client.post(
        reverse("ops-tasks"),
        {
            "assignee": staff.pk,
            "title": "Something",
            "contact": entry_id,
            "organisation": spa.pk,
        },
        content_type="application/json",
    )
    assert response.status_code == 400
    assert response.json()["field"] == "contact"
