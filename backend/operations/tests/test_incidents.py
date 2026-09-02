"""
Incidents and post-mortems.

genmars.co.ke/approach tells the public:

    "Every SEV-1 produces a written post-mortem: what happened, why, and what
     prevents recurrence. Post-mortems are blameless, and they are kept
     permanently."

Charter 04 §IV forbids anything untrue on a Genmars surface. These tests are
what stops that sentence becoming untrue on a busy week.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from operations import services
from portal.models import Incident

pytestmark = pytest.mark.django_db

PASSWORD = "correct-horse-battery"


@pytest.fixture
def staff() -> User:
    return User.objects.create_user(
        email="ops@genmars.co.ke", password=PASSWORD, full_name="Ops",
        is_staff=True, staff_role=User.StaffRole.FOUNDER,
        email_verified_at=timezone.now(),
    )


def _raise(staff, severity=Incident.Severity.SEV1, **kwargs):
    started = kwargs.pop("started_at", timezone.now() - timedelta(hours=3))
    return services.raise_incident(
        actor=staff,
        title=kwargs.pop("title", "Alert email silently discarded"),
        severity=severity,
        started_at=started,
        summary=kwargs.pop("summary", "Every alert was dropped by the provider."),
        **kwargs,
    )


# ── the published promise ────────────────────────────────────────────────────


def test_a_sev1_cannot_be_closed_without_a_post_mortem(staff):
    """
    The whole point. A promise kept by memory is kept until the first busy
    week, and the busy week is when the SEV-1 happens.
    """
    incident = _raise(staff)

    with pytest.raises(services.OperationsError) as caught:
        services.close_incident(incident=incident, actor=staff)

    message = str(caught.value)
    assert "what happened" in message
    assert "why" in message
    assert "what prevents recurrence" in message
    # It names the page that makes the promise, so the refusal is understood
    # rather than resented as bureaucracy.
    assert "genmars.co.ke/approach" in message

    incident.refresh_from_db()
    assert incident.status == Incident.Status.OPEN


def test_the_refusal_names_only_the_parts_still_missing(staff):
    incident = _raise(staff)
    services.write_post_mortem(
        incident=incident, actor=staff,
        what_happened="The provider suppressed the address after a bounce.",
        why="No mailbox existed behind the address.",
    )

    with pytest.raises(services.OperationsError) as caught:
        services.close_incident(incident=incident, actor=staff)

    message = str(caught.value)
    assert "what prevents recurrence" in message
    assert "what happened" not in message


def test_a_complete_post_mortem_lets_it_close(staff):
    incident = _raise(staff)
    services.write_post_mortem(
        incident=incident, actor=staff,
        what_happened="A bounce suppressed the address; everything after was dropped.",
        why="The alert address had no mailbox, and nothing checked deliverability.",
        prevention="Alerts moved to an operator mailbox; a suppression check now "
                   "reports on the operations dashboard.",
    )

    services.close_incident(incident=incident, actor=staff)

    incident.refresh_from_db()
    assert incident.status == Incident.Status.CLOSED
    assert incident.closed_by == staff
    assert incident.resolved_at is not None


def test_lesser_incidents_close_without_one(staff):
    """
    The website promises a post-mortem for SEV-1 and no more. A rule applied to
    everything is a rule that gets worked around.
    """
    for severity in (Incident.Severity.SEV2, Incident.Severity.SEV3):
        incident = _raise(staff, severity=severity)
        services.close_incident(incident=incident, actor=staff)
        incident.refresh_from_db()
        assert incident.status == Incident.Status.CLOSED


def test_whitespace_is_not_a_post_mortem(staff):
    """Three spaces would satisfy a naive truthiness check."""
    incident = _raise(staff)
    services.write_post_mortem(
        incident=incident, actor=staff,
        what_happened="   ", why="  ", prevention=" ",
    )

    with pytest.raises(services.OperationsError):
        services.close_incident(incident=incident, actor=staff)


# ── the timeline ─────────────────────────────────────────────────────────────


def test_how_long_it_ran_unseen_is_computed_not_typed(staff):
    """
    The number that says whether monitoring works. A field would be filled in
    optimistically; this is arithmetic on two facts.
    """
    started = timezone.now() - timedelta(hours=31)
    detected = timezone.now()
    incident = services.raise_incident(
        actor=staff, title="Alerts discarded", severity=Incident.Severity.SEV1,
        started_at=started, detected_at=detected,
        summary="Provider suppressed the alert address.",
    )

    gap = incident.undetected_for()
    assert timedelta(hours=30) < gap < timedelta(hours=32)


def test_a_start_time_must_be_given(staff):
    with pytest.raises(services.OperationsError) as caught:
        services.raise_incident(
            actor=staff, title="Something", severity=Incident.Severity.SEV2,
            started_at=None, summary="It broke.",
        )
    assert "how long it ran unseen" in str(caught.value)


def test_it_cannot_be_detected_before_it_began(staff):
    now = timezone.now()
    with pytest.raises(services.OperationsError):
        services.raise_incident(
            actor=staff, title="Something", severity=Incident.Severity.SEV2,
            started_at=now, detected_at=now - timedelta(hours=1),
            summary="It broke.",
        )


def test_a_title_alone_is_not_a_record(staff):
    with pytest.raises(services.OperationsError) as caught:
        services.raise_incident(
            actor=staff, title="It broke", severity=Incident.Severity.SEV3,
            started_at=timezone.now(), summary="",
        )
    assert "not a record" in str(caught.value)


# ── mitigated is its own state ───────────────────────────────────────────────


def test_mitigated_is_not_closed(staff):
    """
    Collapsing the two is how a workaround quietly becomes the permanent fix.
    """
    incident = _raise(staff)
    services.mitigate_incident(incident=incident, actor=staff)

    incident.refresh_from_db()
    assert incident.status == Incident.Status.MITIGATED
    assert incident.is_open is True
    assert incident.mitigated_at is not None

    # And it still cannot close without the post-mortem.
    with pytest.raises(services.OperationsError):
        services.close_incident(incident=incident, actor=staff)


# ── blameless, and permanent ─────────────────────────────────────────────────


def test_the_model_has_nowhere_to_record_blame(staff):
    """
    A column for who caused it turns the record into an accusation, and
    guarantees the next one is written carefully rather than honestly.
    """
    fields = {f.name for f in Incident._meta.get_fields()}
    for banned in ("responsible", "responsible_person", "at_fault", "blame", "caused_by"):
        assert banned not in fields


def test_closing_keeps_the_record(staff):
    """"Kept permanently" means closing is a status, never a removal."""
    incident = _raise(staff, severity=Incident.Severity.SEV3)
    reference = incident.reference
    services.close_incident(incident=incident, actor=staff)

    assert Incident.objects.filter(reference=reference).exists()


def test_references_do_not_collide(staff):
    references = {
        _raise(staff, severity=Incident.Severity.SEV3).reference for _ in range(4)
    }
    assert len(references) == 4


# ── over HTTP ────────────────────────────────────────────────────────────────


def test_closing_a_sev1_over_http_is_refused_not_a_500(client, staff):
    incident = _raise(staff)
    client.force_login(staff)

    response = client.post(
        reverse("ops-incident-status", args=[incident.pk]),
        {"action": "close"}, content_type="application/json",
    )

    assert response.status_code == 400, response.content
    assert "post-mortem" in str(response.json())
    incident.refresh_from_db()
    assert incident.status == Incident.Status.OPEN


def test_the_full_cycle_over_http(client, staff):
    client.force_login(staff)
    started = (timezone.now() - timedelta(hours=31)).isoformat()

    created = client.post(
        reverse("ops-incidents"),
        {
            "title": "Alert email silently discarded",
            "severity": "sev1",
            "started_at": started,
            "summary": "Every message to the alert address was dropped by the provider.",
            "client_impact": "Mail to info@genmars.co.ke bounced.",
        },
        content_type="application/json",
    )
    assert created.status_code == 201, created.content
    body = created.json()
    assert body["needs_post_mortem"] is True
    assert body["undetected_seconds"] > 30 * 3600

    pk = body["id"]
    written = client.patch(
        reverse("ops-incident", args=[pk]),
        {
            "what_happened": "A bounce suppressed the address.",
            "why": "No mailbox existed behind it.",
            "prevention": "Alerts moved to an operator mailbox.",
        },
        content_type="application/json",
    )
    assert written.status_code == 200, written.content
    assert written.json()["has_post_mortem"] is True

    closed = client.post(
        reverse("ops-incident-status", args=[pk]),
        {"action": "close"}, content_type="application/json",
    )
    assert closed.status_code == 200, closed.content
    assert closed.json()["status"] == "closed"


def test_raising_one_notifies_the_team(staff):
    from portal.models import Notification

    colleague = User.objects.create_user(
        email="asha@genmars.co.ke", password=PASSWORD, full_name="Asha",
        is_staff=True, staff_role=User.StaffRole.COMMERCIAL,
        email_verified_at=timezone.now(),
    )
    _raise(staff)

    rows = Notification.objects.filter(kind=Notification.Kind.INCIDENT_RAISED)
    assert set(rows.values_list("user__email", flat=True)) == {
        staff.email, colleague.email,
    }
    assert all(r.audience == Notification.Audience.STAFF for r in rows)
