"""
The parent-system layer.

These endpoints are the only ones in the project authenticated by a bearer
token rather than a person's session, which makes them the widest surface the
portal exposes. Most of what follows is about how narrow it is in exchange.
"""

from __future__ import annotations

import pytest
from django.urls import reverse
from django.utils import timezone

from accounts.models import Organisation, User
from portal.models import System, SystemEvent, SystemKey
from portal.system_api import authenticate, issue_key

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _no_throttle(settings):
    settings.REST_FRAMEWORK = {
        **settings.REST_FRAMEWORK,
        "DEFAULT_THROTTLE_RATES": {
            **settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"],
            "system": "10000/min",
        },
    }


@pytest.fixture
def owner() -> User:
    return User.objects.create_user(
        email="ops@genmars.co.ke", password="x" * 12, full_name="Ops",
        is_staff=True, staff_role=User.StaffRole.FOUNDER,
        email_verified_at=timezone.now(),
    )


@pytest.fixture
def system(owner) -> System:
    return System.objects.create(
        name="Client Portal", slug="gen-portal", kind=System.Kind.INTERNAL,
        criticality=System.Criticality.CRITICAL,
        purpose="Where clients read scope, progress and invoices.",
        impact_if_down="Clients cannot see their work or pay an invoice.",
        owner=owner,
    )


# ── the credential ───────────────────────────────────────────────────────────


def test_a_token_is_hashed_and_never_stored(system, owner):
    """
    A plaintext credential table beside client contracts and payment records
    would be the softest thing in the building.
    """
    key, token = issue_key(system=system, label="production", actor=owner)

    from django.contrib.auth.hashers import check_password

    assert token.startswith("gms_")

    # Not recoverable from what we keep, and verifiable only by presenting it.
    assert token not in key.hashed
    assert key.hashed != token
    assert check_password(token, key.hashed)

    # It goes through Django's configured hashers, which in production is
    # Argon2 — asserted against the real settings module in
    # accounts/tests/test_identity.py. The suite runs MD5 for speed, so
    # naming an algorithm here would test the test settings.
    assert "$" in key.hashed, "not a Django password hash"

    # The prefix is an index, far too short to be useful on its own.
    assert key.prefix == token[:12]
    assert len(key.prefix) < len(token) / 3


def test_the_right_token_authenticates_and_a_wrong_one_does_not(system, owner):
    key, token = issue_key(system=system, label="production", actor=owner)

    assert authenticate(token) == key
    assert authenticate(token + "x") is None
    assert authenticate("gms_totally-made-up") is None
    assert authenticate("") is None
    # A token without the prefix is refused before any hashing happens.
    assert authenticate(token.removeprefix("gms_")) is None


def test_a_revoked_key_stops_working_but_the_row_survives(system, owner):
    """
    "This key was revoked on the 4th" is a fact worth holding on to; deleting
    it makes an incident harder to reconstruct.
    """
    key, token = issue_key(system=system, label="production", actor=owner)
    SystemKey.objects.filter(pk=key.pk).update(revoked_at=timezone.now())

    assert authenticate(token) is None
    assert SystemKey.objects.filter(pk=key.pk).exists()


def test_two_systems_keys_do_not_cross(owner):
    """A key speaks for one system and there is no admin key."""
    a = System.objects.create(
        name="A", slug="a", kind=System.Kind.INTERNAL,
        criticality=System.Criticality.MINOR, purpose="p",
        impact_if_down="i", owner=owner,
    )
    b = System.objects.create(
        name="B", slug="b", kind=System.Kind.INTERNAL,
        criticality=System.Criticality.MINOR, purpose="p",
        impact_if_down="i", owner=owner,
    )
    _, token_a = issue_key(system=a, label="k", actor=owner)

    assert authenticate(token_a).system == a
    assert authenticate(token_a).system != b


# ── heartbeat ────────────────────────────────────────────────────────────────


def _post(client, url, token, body):
    return client.post(
        url, body, content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )


def test_a_heartbeat_records_that_it_is_running(client, system, owner):
    _, token = issue_key(system=system, label="production", actor=owner)

    response = _post(
        client, reverse("system-heartbeat"), token,
        {"version": "2026.09.03", "health": "up"},
    )

    assert response.status_code == 200, response.content
    system.refresh_from_db()
    assert system.heartbeat_at is not None
    assert system.version == "2026.09.03"
    assert system.health == System.Health.UP


def test_a_system_may_report_itself_unwell(client, system, owner):
    _, token = issue_key(system=system, label="production", actor=owner)

    _post(
        client, reverse("system-heartbeat"), token,
        {"health": "degraded", "detail": "Queue is 4,000 deep."},
    )

    system.refresh_from_db()
    assert system.health == System.Health.DEGRADED
    assert "4,000 deep" in system.health_detail


def test_nonsense_health_is_ignored_rather_than_stored(client, system, owner):
    _, token = issue_key(system=system, label="production", actor=owner)

    _post(client, reverse("system-heartbeat"), token, {"health": "brilliant"})

    system.refresh_from_db()
    assert system.health == System.Health.UNKNOWN
    # But it still counts as having reported in.
    assert system.heartbeat_at is not None


def test_no_token_is_refused_identically_to_a_bad_one(client, system, owner):
    """
    Distinguishing "no such key" from "revoked" would tell an attacker which
    half of the problem to solve.
    """
    url = reverse("system-heartbeat")

    without = client.post(url, {}, content_type="application/json")
    wrong = _post(client, url, "gms_made-up", {})

    assert without.status_code == 401
    assert wrong.status_code == 401
    assert without.json() == wrong.json()


# ── events are data, never commands ──────────────────────────────────────────


def test_an_event_is_recorded_against_its_own_system(client, system, owner):
    _, token = issue_key(system=system, label="production", actor=owner)

    response = _post(
        client, reverse("system-event"), token,
        {"level": "error", "message": "Nightly export failed",
         "detail": {"rows": 0}},
    )

    assert response.status_code == 201, response.content
    event = SystemEvent.objects.get()
    assert event.system == system
    assert event.level == SystemEvent.Level.ERROR
    assert event.detail == {"rows": 0}


def test_an_event_needs_a_message(client, system, owner):
    _, token = issue_key(system=system, label="production", actor=owner)
    response = _post(client, reverse("system-event"), token, {"level": "error"})
    assert response.status_code == 400


def test_an_unknown_level_falls_back_rather_than_failing(client, system, owner):
    _, token = issue_key(system=system, label="production", actor=owner)
    _post(client, reverse("system-event"), token,
          {"level": "catastrophic", "message": "Something"})

    assert SystemEvent.objects.get().level == SystemEvent.Level.INFO


def test_a_huge_or_non_object_detail_is_dropped(client, system, owner):
    """A child must not be able to fill the column with a megabyte of nesting."""
    _, token = issue_key(system=system, label="production", actor=owner)

    _post(client, reverse("system-event"), token,
          {"message": "a", "detail": "not an object"})
    _post(client, reverse("system-event"), token,
          {"message": "b", "detail": {"x": "y" * 5000}})

    for event in SystemEvent.objects.all():
        assert event.detail == {}


def test_message_text_is_stored_verbatim_and_never_interpreted(client, system, owner):
    """
    Everything here arrived over the network from another application. It is
    recorded and rendered as text — never evaluated, never used to build a
    query, never treated as a path.
    """
    _, token = issue_key(system=system, label="production", actor=owner)
    hostile = "<script>alert(1)</script> '; DROP TABLE portal_system; --"

    _post(client, reverse("system-event"), token, {"message": hostile})

    event = SystemEvent.objects.get()
    assert event.message == hostile
    assert System.objects.filter(slug="gen-portal").exists()


def test_a_key_cannot_read_anything(client, system, owner):
    """
    A leaked key leaks nothing. It can write noise into one system's event feed,
    which a human then reads and disbelieves.
    """
    _, token = issue_key(system=system, label="production", actor=owner)
    headers = {"HTTP_AUTHORIZATION": f"Bearer {token}"}

    for name in ("order-list", "invoice-list", "notifications", "service-catalogue"):
        response = client.get(reverse(name), **headers)
        assert response.status_code == 403, f"{name} answered {response.status_code}"

    assert client.get(reverse("ops-overview"), **headers).status_code == 403


# ── knowing whether something is alive ───────────────────────────────────────


def test_a_stale_heartbeat_is_distinct_from_never_having_sent_one(system):
    """
    A system that has never reported may simply not be instrumented — a gap in
    our knowledge, not evidence of a fault.
    """
    from datetime import timedelta

    assert system.heartbeat_is_stale() is False
    assert system.is_watched is False

    system.heartbeat_at = timezone.now() - timedelta(hours=2)
    assert system.heartbeat_is_stale(minutes=30) is True
    assert system.is_watched is True

    system.heartbeat_at = timezone.now()
    assert system.heartbeat_is_stale(minutes=30) is False


def test_a_client_owned_system_is_marked_as_theirs(owner):
    """Charter 04 §V — we monitor it because we were asked to. It is not ours."""
    org = Organisation.objects.create(name="Kilimani Dental")
    system = System.objects.create(
        name="Kilimani booking", slug="kilimani-booking", kind=System.Kind.CLIENT,
        criticality=System.Criticality.IMPORTANT, purpose="Bookings.",
        impact_if_down="Patients cannot book.", owner=owner, organisation=org,
    )
    assert system.organisation == org


# ── the health poller ────────────────────────────────────────────────────────


def test_a_bad_response_and_no_response_are_different_states(system, owner, monkeypatch):
    """
    A 500 and a refused connection are different problems. Collapsing them
    loses the distinction exactly when it matters.
    """
    import urllib.error

    from operations.management.commands import check_systems

    probe = check_systems.Command._probe

    def http_error(request, **kwargs):
        raise urllib.error.HTTPError("https://example.test", 500, "boom", {}, None)

    def unreachable(request, **kwargs):
        raise urllib.error.URLError("no route to host")

    monkeypatch.setattr(check_systems.urllib.request, "urlopen", http_error)
    assert probe("https://example.test/health")[0] == System.Health.DEGRADED

    monkeypatch.setattr(check_systems.urllib.request, "urlopen", unreachable)
    assert probe("https://example.test/health")[0] == System.Health.DOWN


def test_one_unreachable_system_does_not_stop_the_others(owner, monkeypatch, capsys):
    """
    A command that dies on the first failure stops checking the rest — which is
    backwards, since those are the ones you now know nothing about.
    """
    import urllib.error

    from django.core.management import call_command

    from operations.management.commands import check_systems

    for i in range(3):
        System.objects.create(
            name=f"S{i}", slug=f"s{i}", kind=System.Kind.INTERNAL,
            criticality=System.Criticality.MINOR, purpose="p",
            impact_if_down="i", owner=owner,
            health_url=f"https://example.test/{i}/health",
        )

    def unreachable(*args, **kwargs):
        raise urllib.error.URLError("down")

    monkeypatch.setattr(check_systems.urllib.request, "urlopen", unreachable)

    with pytest.raises(SystemExit):
        call_command("check_systems")

    # Every one was checked and recorded, not just the first.
    assert System.objects.filter(health=System.Health.DOWN).count() == 3
    assert System.objects.filter(checked_at__isnull=True).count() == 0


def test_a_system_with_no_health_url_is_not_reported_as_down(owner):
    """Not instrumented is a gap in our knowledge, not a fault."""
    from django.core.management import call_command

    System.objects.create(
        name="Unwatched", slug="unwatched", kind=System.Kind.INTERNAL,
        criticality=System.Criticality.MINOR, purpose="p",
        impact_if_down="i", owner=owner,
    )
    call_command("check_systems")

    system = System.objects.get(slug="unwatched")
    assert system.health == System.Health.UNKNOWN
