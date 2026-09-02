"""
The security tier tracker.

genmars.co.ke/approach publishes fifteen requirements across three tiers and
calls each tier "a gate, not a wish list", with Tier 1 stated as the bar before
any client system goes live. These tests are what stops that being a
description of an intention rather than of anything that happens.
"""

from __future__ import annotations

import pytest
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from accounts.models import Organisation, User
from portal.models import SecurityCheck, System

pytestmark = pytest.mark.django_db


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
        criticality=System.Criticality.CRITICAL, purpose="p",
        impact_if_down="Clients cannot see their work.", owner=owner,
    )


def _satisfy(system, tier, status=SecurityCheck.Status.MET, note="Evidence."):
    system.security_checks.filter(tier=tier).update(status=status, note=note)


# ── the seed matches what is published ───────────────────────────────────────


def test_every_published_requirement_is_created(system):
    call_command("seed_security_checks", verbosity=0)

    assert system.security_checks.count() == 15
    assert system.security_checks.filter(tier=SecurityCheck.Tier.ONE).count() == 6
    assert system.security_checks.filter(tier=SecurityCheck.Tier.TWO).count() == 5
    assert system.security_checks.filter(tier=SecurityCheck.Tier.THREE).count() == 4


def test_the_wording_is_the_websites_wording(system):
    """
    Copied verbatim, so a check recorded today still says what was being
    assessed even if the page is reworded later.
    """
    call_command("seed_security_checks", verbosity=0)

    items = set(system.security_checks.values_list("item", flat=True))
    assert (
        "Automated backups with a tested restore — an untested backup is not a backup"
        in items
    )
    assert "TLS everywhere; no plaintext transport, internal or external" in items


def test_reseeding_never_touches_an_assessment(system, owner):
    """
    An assessment is somebody's judgement with their name against it. A command
    that quietly reset those would destroy the only record of who said what,
    and would do it on the next deploy.
    """
    call_command("seed_security_checks", verbosity=0)
    check = system.security_checks.first()
    check.status = SecurityCheck.Status.MET
    check.note = "Argon2, staff roles, tested."
    check.assessed_by = owner
    check.save()

    call_command("seed_security_checks", verbosity=0)

    check.refresh_from_db()
    assert check.status == SecurityCheck.Status.MET
    assert check.note == "Argon2, staff roles, tested."
    assert check.assessed_by == owner
    assert system.security_checks.count() == 15


# ── the gate arithmetic ──────────────────────────────────────────────────────


def test_a_system_starts_below_tier_one(system):
    call_command("seed_security_checks", verbosity=0)
    assert system.security_tier_met() is None


def test_tiers_are_sequential(system):
    """
    Tier 2 without Tier 1 is not Tier 2 — it is a system with an audit log and
    no backups. Counting the highest tier that individually passed would let a
    gap in the foundation hide behind work done further up.
    """
    call_command("seed_security_checks", verbosity=0)

    _satisfy(system, SecurityCheck.Tier.TWO)
    assert system.security_tier_met() is None, "Tier 2 alone is not a tier"

    _satisfy(system, SecurityCheck.Tier.ONE)
    assert system.security_tier_met() == SecurityCheck.Tier.TWO


def test_partly_met_does_not_pass_a_gate(system):
    """Mostly through a gate is on the wrong side of it."""
    call_command("seed_security_checks", verbosity=0)
    _satisfy(system, SecurityCheck.Tier.ONE)
    assert system.security_tier_met() == SecurityCheck.Tier.ONE

    one = system.security_checks.filter(tier=SecurityCheck.Tier.ONE).first()
    one.status = SecurityCheck.Status.PARTIAL
    one.save()

    assert system.security_tier_met() is None


def test_not_applicable_counts_as_satisfied(system):
    """
    It has to, or a requirement that genuinely does not apply would hold a
    system below a gate forever. It is also the state most able to turn a red
    board green without anything changing, which is why it needs a note.
    """
    call_command("seed_security_checks", verbosity=0)
    _satisfy(system, SecurityCheck.Tier.ONE)

    one = system.security_checks.filter(tier=SecurityCheck.Tier.ONE).first()
    one.status = SecurityCheck.Status.NOT_APPLICABLE
    one.note = "No client data passes through this system at all."
    one.save()

    assert system.security_tier_met() == SecurityCheck.Tier.ONE


def test_partial_and_not_applicable_demand_an_explanation(system):
    call_command("seed_security_checks", verbosity=0)
    check = system.security_checks.first()

    for status in (SecurityCheck.Status.PARTIAL, SecurityCheck.Status.NOT_APPLICABLE):
        check.status = status
        check.note = ""
        assert check.needs_a_note is True, status
        check.note = "Because of this specific thing."
        assert check.needs_a_note is False

    # Met and not-met do not: the requirement says what was needed, and an
    # absence is its own explanation.
    for status in (SecurityCheck.Status.MET, SecurityCheck.Status.NOT_MET):
        check.status = status
        check.note = ""
        assert check.needs_a_note is False, status


# ── the published gate ───────────────────────────────────────────────────────


def test_a_live_client_system_below_tier_one_is_flagged(owner):
    """
    The page says Tier 1 is the bar "before any client system goes live". A
    live system below it is a commitment being broken, so it is computed rather
    than left to somebody noticing.
    """
    org = Organisation.objects.create(name="Kilimani Dental")
    system = System.objects.create(
        name="Kilimani booking", slug="kilimani", kind=System.Kind.CLIENT,
        criticality=System.Criticality.IMPORTANT, purpose="Bookings.",
        impact_if_down="Patients cannot book.", owner=owner, organisation=org,
        status=System.Status.LIVE,
    )
    call_command("seed_security_checks", slug="kilimani", verbosity=0)

    assert system.fails_tier_one() is True

    _satisfy(system, SecurityCheck.Tier.ONE)
    assert system.fails_tier_one() is False


def test_a_system_that_is_not_live_is_not_failing_a_go_live_gate(owner):
    system = System.objects.create(
        name="Half built", slug="half-built", kind=System.Kind.PRODUCT,
        criticality=System.Criticality.MINOR, purpose="p", impact_if_down="i",
        owner=owner, status=System.Status.BUILDING,
    )
    call_command("seed_security_checks", slug="half-built", verbosity=0)

    assert system.security_tier_met() is None
    assert system.fails_tier_one() is False, "not live, so no go-live gate to fail"


def test_an_unassessed_system_is_not_reported_as_failing(owner):
    """
    Never assessed is not the same as failing, and not the same as passing. The
    screen shows it as unassessed rather than green.
    """
    system = System.objects.create(
        name="New thing", slug="new-thing", kind=System.Kind.INTERNAL,
        criticality=System.Criticality.MINOR, purpose="p", impact_if_down="i",
        owner=owner, status=System.Status.LIVE,
    )
    assert system.security_checks.count() == 0
    assert system.fails_tier_one() is False
    assert system.security_tier_met() is None


# ── over HTTP ────────────────────────────────────────────────────────────────


def test_marking_not_applicable_without_a_reason_is_refused(client, system, owner):
    """
    It is the state most able to turn a red board green without anything
    changing, so the reason has to be on record. Refused rather than saved and
    flagged — a flag on a saved row is something to ignore later.
    """
    call_command("seed_security_checks", verbosity=0)
    check = system.security_checks.first()

    client.force_login(owner)
    response = client.patch(
        reverse("ops-security-check", args=[system.slug, check.pk]),
        {"status": "n_a", "note": ""},
        content_type="application/json",
    )

    assert response.status_code == 400
    assert "published gate" in str(response.json())

    check.refresh_from_db()
    assert check.status == SecurityCheck.Status.NOT_MET


def test_partial_without_a_reason_is_refused_too(client, system, owner):
    call_command("seed_security_checks", verbosity=0)
    check = system.security_checks.first()

    client.force_login(owner)
    response = client.patch(
        reverse("ops-security-check", args=[system.slug, check.pk]),
        {"status": "partial", "note": "   "},
        content_type="application/json",
    )

    assert response.status_code == 400
    assert "cannot be acted on" in str(response.json())


def test_recording_a_check_names_who_said_it(client, system, owner):
    call_command("seed_security_checks", verbosity=0)
    check = system.security_checks.get(
        tier=SecurityCheck.Tier.ONE, position=3
    )

    client.force_login(owner)
    response = client.patch(
        reverse("ops-security-check", args=[system.slug, check.pk]),
        {"status": "met",
         "note": "Nightly dump, weekly restore test, off-box copy proven 2026-09-02."},
        content_type="application/json",
    )

    assert response.status_code == 200, response.content
    body = response.json()
    assert body["check"]["assessed_by_name"] == "Ops"
    assert body["check"]["assessed_at"] is not None
    # And the system's tier is recomputed in the same response, so the screen
    # cannot show a stale gate beside a fresh assessment.
    assert "security_tier" in body["system"]


def test_the_system_list_says_unassessed_rather_than_passing(client, system, owner):
    client.force_login(owner)
    body = client.get(reverse("ops-systems")).json()
    shown = next(s for s in body["systems"] if s["slug"] == system.slug)

    assert shown["security_assessed"] is False
    assert shown["security_tier"] is None
    assert shown["fails_tier_one"] is False
