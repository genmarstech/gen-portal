"""
The workroom: clocking in and out, streaks, and the decision register.

═══════════════════════════════════════════════════════════════════════════════
THE TEST THAT MATTERS HERE IS test_nobody_can_clock_anybody_else_in.

A timesheet somebody else can write is not a timesheet. Everything else in
this file is arithmetic; that one is the property the feature rests on, and it
is the one an endpoint could quietly lose by growing a `person` field.
═══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

import pytest
from django.db.utils import IntegrityError
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from operations import selectors, services
from portal.models import ActivityLog, Decision, Shift

pytestmark = pytest.mark.django_db

PASSWORD = "correct-horse-battery"


def _staff(email: str, role: str = User.StaffRole.DELIVERY, name: str = "") -> User:
    return User.objects.create_user(
        email=email,
        password=PASSWORD,
        full_name=name,
        is_staff=True,
        staff_role=role,
        email_verified_at=timezone.now(),
    )


@pytest.fixture
def asha() -> User:
    return _staff("asha@genmars.co.ke", User.StaffRole.DELIVERY, "Asha Mwangi")


@pytest.fixture
def founder() -> User:
    return _staff("founder@genmars.co.ke", User.StaffRole.FOUNDER, "The Founder")


def _at(day: date, hour: int, minute: int = 0):
    """An aware datetime in Nairobi, which is what every row here stores."""
    return timezone.make_aware(
        datetime.combine(day, time(hour, minute)), timezone.get_current_timezone()
    )


# ── clocking in and out ──────────────────────────────────────────────────────


def test_clocking_in_and_out_records_the_time_worked(client, asha):
    client.force_login(asha)

    started = client.post(
        reverse("ops-clock"),
        {"action": "in", "note": "M-Pesa reconciliation"},
        content_type="application/json",
    )
    assert started.status_code == 200
    assert started.json()["open"]["started_note"] == "M-Pesa reconciliation"

    shift = Shift.objects.get(person=asha)
    shift.started_at = timezone.now() - timedelta(hours=3)
    shift.save(update_fields=["started_at"])

    ended = client.post(
        reverse("ops-clock"), {"action": "out"}, content_type="application/json"
    )
    assert ended.status_code == 200
    assert ended.json()["open"] is None

    shift.refresh_from_db()
    assert shift.ended_at is not None
    assert 175 <= shift.minutes <= 185
    assert shift.ended_late is False


def test_nobody_can_clock_anybody_else_in(client, founder, asha):
    """
    The endpoint takes no person, so a founder posting one clocks THEMSELVES.

    Not a 400: the field is simply not read. That is the stronger property —
    a refusal could be lost by a serializer change, whereas there being no
    argument to pass means the mistake has nowhere to land.
    """
    client.force_login(founder)
    response = client.post(
        reverse("ops-clock"),
        {"action": "in", "person": asha.pk, "person_id": asha.pk},
        content_type="application/json",
    )
    assert response.status_code == 200

    assert Shift.objects.filter(person=founder).count() == 1
    assert not Shift.objects.filter(person=asha).exists()


def test_clocking_in_twice_is_refused(client, asha):
    client.force_login(asha)
    client.post(reverse("ops-clock"), {"action": "in"}, content_type="application/json")
    again = client.post(
        reverse("ops-clock"), {"action": "in"}, content_type="application/json"
    )
    assert again.status_code == 400
    assert "clocked in" in again.json()["detail"]
    assert Shift.objects.filter(person=asha).count() == 1


def test_the_database_itself_refuses_a_second_open_shift(asha):
    """
    Not just the service. Two taps on a slow connection race past the SELECT,
    and the second row would double every hour that person worked that day.
    """
    Shift.objects.create(person=asha)
    with pytest.raises(IntegrityError):
        Shift.objects.create(person=asha)


def test_clocking_out_when_not_in_is_refused(client, asha):
    client.force_login(asha)
    response = client.post(
        reverse("ops-clock"), {"action": "out"}, content_type="application/json"
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "You are not clocked in."


def test_a_forgotten_shift_will_not_close_at_now(client, asha):
    """
    The whole reason STALE_SHIFT exists: one nineteen-hour day poisons every
    total on the timesheet and nothing about the row says it is wrong.
    """
    client.force_login(asha)
    shift = Shift.objects.create(person=asha)
    shift.started_at = timezone.now() - timedelta(hours=20)
    shift.save(update_fields=["started_at"])

    refused = client.post(
        reverse("ops-clock"), {"action": "out"}, content_type="application/json"
    )
    assert refused.status_code == 400
    assert refused.json()["field"] == "ended_at"

    shift.refresh_from_db()
    assert shift.ended_at is None


def test_a_forgotten_shift_closes_with_a_time_and_is_marked_as_remembered(client, asha):
    client.force_login(asha)
    shift = Shift.objects.create(person=asha)
    shift.started_at = timezone.now() - timedelta(hours=20)
    shift.save(update_fields=["started_at"])

    ended = client.post(
        reverse("ops-clock"),
        {
            "action": "out",
            "ended_at": (shift.started_at + timedelta(hours=6)).isoformat(),
        },
        content_type="application/json",
    )
    assert ended.status_code == 200

    shift.refresh_from_db()
    assert shift.minutes == 360
    # An hour that was remembered is a weaker fact than one that was measured,
    # and the row keeps the difference.
    assert shift.ended_late is True


def test_a_shift_cannot_end_before_it_started(client, asha):
    client.force_login(asha)
    shift = Shift.objects.create(person=asha)
    shift.started_at = timezone.now() - timedelta(hours=20)
    shift.save(update_fields=["started_at"])

    response = client.post(
        reverse("ops-clock"),
        {"action": "out", "ended_at": (shift.started_at - timedelta(hours=1)).isoformat()},
        content_type="application/json",
    )
    assert response.status_code == 400
    assert response.json()["field"] == "ended_at"


def test_a_shift_cannot_end_in_the_future(client, asha):
    client.force_login(asha)
    shift = Shift.objects.create(person=asha)
    shift.started_at = timezone.now() - timedelta(hours=20)
    shift.save(update_fields=["started_at"])

    response = client.post(
        reverse("ops-clock"),
        {"action": "out", "ended_at": (timezone.now() + timedelta(hours=1)).isoformat()},
        content_type="application/json",
    )
    assert response.status_code == 400
    assert "future" in response.json()["detail"]


def test_clocking_is_written_to_the_log_with_a_name(client, asha):
    client.force_login(asha)
    client.post(reverse("ops-clock"), {"action": "in"}, content_type="application/json")
    client.post(reverse("ops-clock"), {"action": "out"}, content_type="application/json")

    actions = list(
        ActivityLog.objects.filter(actor=asha).values_list("action", flat=True)
    )
    assert ActivityLog.Action.SHIFT_STARTED in actions
    assert ActivityLog.Action.SHIFT_ENDED in actions

    entry = ActivityLog.objects.filter(action=ActivityLog.Action.SHIFT_STARTED).first()
    assert entry.actor_label == "Asha Mwangi"


def test_who_is_in_shows_only_open_shifts(client, asha, founder):
    Shift.objects.create(person=asha, started_note="On GM-2026-0001")
    Shift.objects.create(
        person=founder,
        started_at=timezone.now() - timedelta(hours=2),
        ended_at=timezone.now(),
    )

    client.force_login(founder)
    body = client.get(reverse("ops-clock")).json()
    names = [p["name"] for p in body["who_is_in"]]
    assert names == ["Asha Mwangi"]


def test_the_header_knows_a_shift_is_stale_before_the_button_is_pressed(client, asha):
    """A control that fails on click reads as broken. It says so first."""
    client.force_login(asha)
    shift = Shift.objects.create(person=asha)
    shift.started_at = timezone.now() - timedelta(hours=20)
    shift.save(update_fields=["started_at"])

    assert client.get(reverse("ops-clock")).json()["stale"] is True


# ── streaks ──────────────────────────────────────────────────────────────────


def _worked(person: User, *days: date) -> None:
    for day in days:
        Shift.objects.create(
            person=person, started_at=_at(day, 9), ended_at=_at(day, 17)
        )


def test_a_weekend_does_not_break_a_streak(asha):
    """
    The whole design. A counter that resets every Saturday measures whether
    somebody worked the weekend, which is the opposite of the point.
    """
    friday = date(2026, 8, 28)
    monday = date(2026, 8, 31)
    tuesday = date(2026, 9, 1)
    _worked(asha, friday, monday, tuesday)

    streak = selectors.working_streak(asha, today=tuesday)
    assert streak["current"] == 3


def test_a_missed_working_day_does_break_it(asha):
    monday = date(2026, 8, 31)
    # Tuesday missed.
    wednesday = date(2026, 9, 2)
    _worked(asha, monday, wednesday)

    assert selectors.working_streak(asha, today=wednesday)["current"] == 1


def test_the_streak_survives_the_morning_before_anyone_clocks_in(asha):
    """
    Counted strictly to today it would read zero every morning, which turns a
    quiet motivator into a daily accusation.
    """
    monday = date(2026, 8, 31)
    tuesday = date(2026, 9, 1)
    _worked(asha, monday, tuesday)

    wednesday = date(2026, 9, 2)
    streak = selectors.working_streak(asha, today=wednesday)
    assert streak["current"] == 2
    assert streak["worked_today"] is False


def test_a_weekend_day_worked_counts_towards_the_streak(asha):
    friday = date(2026, 8, 28)
    saturday = date(2026, 8, 29)
    monday = date(2026, 8, 31)
    _worked(asha, friday, saturday, monday)

    assert selectors.working_streak(asha, today=monday)["current"] == 3


def test_someone_who_has_never_clocked_in_has_no_streak_rather_than_an_error(asha):
    assert selectors.working_streak(asha) == {
        "current": 0,
        "longest": 0,
        "last_worked": None,
        "worked_today": False,
    }


def test_the_longest_streak_survives_a_later_break(asha):
    _worked(
        asha,
        date(2026, 8, 24),  # Mon
        date(2026, 8, 25),
        date(2026, 8, 26),
        date(2026, 8, 27),
        date(2026, 8, 28),  # Fri — five
        # Mon 31st missed, so the run ends.
        date(2026, 9, 1),
    )
    streak = selectors.working_streak(asha, today=date(2026, 9, 1))
    assert streak["current"] == 1
    assert streak["longest"] == 5


def test_a_shift_belongs_to_the_day_it_started_not_the_day_it_ended(asha):
    """
    Splitting a past-midnight shift would put an hour of Monday into Tuesday
    and break Monday's place in a streak.
    """
    monday = date(2026, 8, 31)
    shift = Shift.objects.create(
        person=asha, started_at=_at(monday, 22), ended_at=_at(monday + timedelta(days=1), 1)
    )
    assert shift.local_date == monday
    assert shift.minutes == 180


# ── the timesheet ────────────────────────────────────────────────────────────


def test_the_timesheet_totals_each_person(client, asha, founder):
    today = timezone.localdate()
    _worked(asha, today, today - timedelta(days=1))
    _worked(founder, today)

    client.force_login(asha)
    body = client.get(reverse("ops-timesheet")).json()
    rows = {p["email"]: p for p in body["people"]}

    assert rows["asha@genmars.co.ke"]["minutes"] == 960
    assert rows["asha@genmars.co.ke"]["days"] == 2
    assert rows["founder@genmars.co.ke"]["minutes"] == 480


def test_everyone_sees_everyone(client, asha, founder):
    """
    Read is shared here as everywhere else in operations. A timesheet only the
    founder could read would be surveillance; one everybody reads is a rota.
    """
    _worked(founder, timezone.localdate())
    client.force_login(asha)

    body = client.get(reverse("ops-timesheet")).json()
    assert any(p["email"] == "founder@genmars.co.ke" for p in body["people"])


def test_the_timesheet_window_is_clamped(client, asha):
    client.force_login(asha)
    assert client.get(reverse("ops-timesheet"), {"days": "9999"}).json()["days"] == 90
    assert client.get(reverse("ops-timesheet"), {"days": "0"}).json()["days"] == 1
    assert client.get(reverse("ops-timesheet"), {"days": "nonsense"}).json()["days"] == 14


def test_an_unknown_person_filter_is_a_404_not_an_empty_timesheet(client, asha):
    client.force_login(asha)
    assert client.get(reverse("ops-timesheet"), {"person": "99999"}).status_code == 404


def test_a_client_account_cannot_read_the_timesheet(client):
    """Where everyone works and when is not a client's business."""
    outsider = User.objects.create_user(
        email="client@example.com", password=PASSWORD, email_verified_at=timezone.now()
    )
    client.force_login(outsider)
    assert client.get(reverse("ops-timesheet")).status_code == 403
    assert client.get(reverse("ops-clock")).status_code == 403


# ── the decision register ────────────────────────────────────────────────────


def _record(client, **overrides):
    body = {
        "title": "Bill in KES only",
        "context": "Two of our first four enquiries asked to be billed in USD.",
        "decision": "Every invoice is issued in KES.",
        "options": "Dual-currency invoices; a USD price list.",
        "consequences": "We lose enquiries that insist on USD.",
        "revisit_when": "A client over KES 2M a year needs USD.",
    }
    body.update(overrides)
    return client.post(reverse("ops-decisions"), body, content_type="application/json")


def test_a_decision_is_recorded_with_a_reference_and_an_author(client, founder):
    client.force_login(founder)
    response = _record(client)
    assert response.status_code == 201

    body = response.json()
    assert body["reference"].startswith("GM-DEC-")
    assert body["status"] == "decided"
    assert body["decided_by"] == "The Founder"
    assert body["decided_on"] is not None


def test_a_decision_without_its_context_is_refused(client, founder):
    """
    The field that stops being obvious first. Without it the entry reads in six
    months as an arbitrary preference — which is how a decision gets undone and
    the original problem comes back.
    """
    client.force_login(founder)
    response = _record(client, context="   ")
    assert response.status_code == 400
    assert response.json()["field"] == "context"


def test_any_staff_account_may_record_one(client, asha):
    """
    Not a permission system. Gating the writing-down of a decision on rank
    produces decisions that were made and never written, which is the failure
    the register exists to prevent.
    """
    client.force_login(asha)
    assert _record(client).status_code == 201


def test_a_decided_entry_cannot_be_edited(client, founder):
    client.force_login(founder)
    pk = _record(client).json()["id"]

    response = client.patch(
        reverse("ops-decision", args=[pk]),
        {"action": "revise", "decision": "Actually we bill in USD."},
        content_type="application/json",
    )
    assert response.status_code == 400
    assert "supersedes it" in response.json()["detail"]

    assert Decision.objects.get(pk=pk).decision == "Every invoice is issued in KES."


def test_a_proposal_can_be_edited_and_then_decided(client, asha):
    client.force_login(asha)
    pk = _record(client, status="proposed").json()["id"]

    revised = client.patch(
        reverse("ops-decision", args=[pk]),
        {"action": "revise", "title": "Bill in KES, quote in KES"},
        content_type="application/json",
    )
    assert revised.status_code == 200
    assert revised.json()["title"] == "Bill in KES, quote in KES"
    assert revised.json()["decided_by"] == ""

    decided = client.patch(
        reverse("ops-decision", args=[pk]),
        {"action": "decide"},
        content_type="application/json",
    )
    assert decided.status_code == 200
    assert decided.json()["status"] == "decided"
    assert decided.json()["decided_by"] == "Asha Mwangi"


def test_superseding_leaves_the_original_readable(client, founder):
    """
    The wrong turns are most of the value. A register that drops them teaches
    the same lesson twice.
    """
    client.force_login(founder)
    first = _record(client).json()

    second = _record(
        client,
        title="Bill in KES, accept USD over KES 2M",
        supersedes=first["id"],
    )
    assert second.status_code == 201

    original = Decision.objects.get(pk=first["id"])
    assert original.status == Decision.Status.SUPERSEDED
    assert original.decision == "Every invoice is issued in KES."

    assert second.json()["supersedes"]["reference"] == first["reference"]

    body = client.get(reverse("ops-decision", args=[first["id"]])).json()
    assert body["superseded_by"]["reference"] == second.json()["reference"]


def test_a_superseded_entry_cannot_be_superseded_again(client, founder):
    client.force_login(founder)
    first = _record(client).json()
    _record(client, supersedes=first["id"])

    third = _record(client, supersedes=first["id"])
    assert third.status_code == 400
    assert third.json()["field"] == "supersedes"


def test_a_proposal_supersedes_nothing_yet(client, founder):
    client.force_login(founder)
    first = _record(client).json()
    response = _record(client, status="proposed", supersedes=first["id"])
    assert response.status_code == 400
    assert Decision.objects.get(pk=first["id"]).status == Decision.Status.DECIDED


def test_reversing_requires_a_reason(client, founder):
    client.force_login(founder)
    pk = _record(client).json()["id"]

    response = client.patch(
        reverse("ops-decision", args=[pk]),
        {"action": "reverse", "reason": "  "},
        content_type="application/json",
    )
    assert response.status_code == 400
    assert response.json()["field"] == "reason"
    assert Decision.objects.get(pk=pk).status == Decision.Status.DECIDED


def test_a_reversal_keeps_its_reason_and_the_original_wording(client, founder):
    client.force_login(founder)
    pk = _record(client).json()["id"]

    response = client.patch(
        reverse("ops-decision", args=[pk]),
        {"action": "reverse", "reason": "Safaricom now settles USD to a KES float."},
        content_type="application/json",
    )
    assert response.status_code == 200

    entry = Decision.objects.get(pk=pk)
    assert entry.status == Decision.Status.REVERSED
    assert "Safaricom" in entry.reversal_reason
    assert entry.decision == "Every invoice is issued in KES."


def test_reversed_and_superseded_entries_stay_in_the_list(client, founder):
    client.force_login(founder)
    pk = _record(client).json()["id"]
    client.patch(
        reverse("ops-decision", args=[pk]),
        {"action": "reverse", "reason": "It cost us two clients."},
        content_type="application/json",
    )

    body = client.get(reverse("ops-decisions")).json()
    assert [d["id"] for d in body["decisions"]] == [pk]


def test_the_register_is_searchable(client, founder):
    client.force_login(founder)
    _record(client)
    _record(client, title="Run Postgres ourselves", context="Managed Postgres in Nairobi is scarce.")

    hits = client.get(reverse("ops-decisions"), {"q": "Postgres"}).json()["decisions"]
    assert len(hits) == 1
    assert hits[0]["title"] == "Run Postgres ourselves"


def test_every_register_write_is_logged(client, founder):
    client.force_login(founder)
    pk = _record(client).json()["id"]
    client.patch(
        reverse("ops-decision", args=[pk]),
        {"action": "reverse", "reason": "Wrong call."},
        content_type="application/json",
    )

    actions = set(ActivityLog.objects.values_list("action", flat=True))
    assert ActivityLog.Action.DECISION_MADE in actions
    assert ActivityLog.Action.DECISION_REVERSED in actions


def test_a_client_account_cannot_read_the_register(client, founder):
    client.force_login(founder)
    _record(client)
    client.logout()

    outsider = User.objects.create_user(
        email="client@example.com", password=PASSWORD, email_verified_at=timezone.now()
    )
    client.force_login(outsider)
    assert client.get(reverse("ops-decisions")).status_code == 403
