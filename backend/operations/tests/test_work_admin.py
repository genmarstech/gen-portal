"""
Who may change the portfolio, and what operations is told about it.

═══════════════════════════════════════════════════════════════════════════════
THE ONE THAT MATTERS IS test_consent_holds_even_when_published_is_ticked.

Charter 04 §V: a client is named in public only with written permission. The
tick box on the operations screen is "include this in the next build" — it is
NOT permission, and somebody will eventually tick it for a client project
before the signature exists. When that happens the item must stay out of the
website's payload, and operations must SAY SO rather than reporting it live.

Both halves are tested here: the gate holds, and `is_publishable` /
`awaiting_consent` tell the truth about it. A gate that holds silently is how
a founder ends up believing a client's project is on the site for a month.
═══════════════════════════════════════════════════════════════════════════════

Charter 02 §I puts public statements with the founder, so writing is founder
only, same as the documentation beside it.
"""

from __future__ import annotations

import pytest
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from portal.models import ActivityLog, WorkItem

pytestmark = pytest.mark.django_db


def staff(email: str, role: str = "") -> User:
    user = User.objects.create_user(email=email, password="x", full_name="A Person")
    user.is_staff = True
    user.staff_role = role
    user.email_verified_at = timezone.now()
    user.save()
    return user


@pytest.fixture
def founder() -> User:
    return staff("founder@genmars.co.ke", "founder")


@pytest.fixture
def engineer() -> User:
    return staff("engineer@genmars.co.ke", "delivery")


# ── the state migration 0041 actually leaves behind ─────────────────────────
#
# These are not invented fixtures. Migration 0041 carried the three things
# Genmars had built into the table, and every test below runs against that,
# because it is what the founder will open this screen and see. The first
# draft of this file created its own rows and collided with all three.

SEEDED_LIVE = 1  # the platform: ours, so nobody's permission to seek
SEEDED_HELD = 0  # the two client sites are unpublished, not held


@pytest.fixture
def platform() -> WorkItem:
    """Ours. Published, and needs no signature from anybody."""
    return WorkItem.objects.get(slug="business-platform")


@pytest.fixture
def client_site() -> WorkItem:
    """A real client's, seeded unpublished and without permission on file."""
    return WorkItem.objects.get(slug="clips-serenity-spa")


NEW = {
    "slug": "a-client-booking-site",
    "name": "A Client Booking Site",
    "category": "sites",
    "label": "client",
    "summary": "A booking site for a salon in Nairobi.",
}


# ── who may write ───────────────────────────────────────────────────────────


def test_any_staff_may_read_the_work(client, engineer, platform):
    client.force_login(engineer)
    response = client.get(reverse("ops-work"))
    assert response.status_code == 200
    assert response.json()["may_edit"] is False


def test_a_non_founder_cannot_create(client, engineer):
    client.force_login(engineer)
    response = client.post(
        reverse("ops-work"), NEW, content_type="application/json"
    )
    assert response.status_code == 403
    assert not WorkItem.objects.filter(slug=NEW["slug"]).exists()


def test_a_non_founder_cannot_edit(client, engineer, platform):
    client.force_login(engineer)
    response = client.patch(
        reverse("ops-work-item", args=[platform.pk]),
        {"name": "Something else"},
        content_type="application/json",
    )
    assert response.status_code == 403
    platform.refresh_from_db()
    assert platform.name == "Genmars Business Platform"


def test_a_non_founder_cannot_delete(client, engineer, platform):
    client.force_login(engineer)
    response = client.delete(reverse("ops-work-item", args=[platform.pk]))
    assert response.status_code == 403
    assert WorkItem.objects.filter(pk=platform.pk).exists()


def test_the_founder_can_create_edit_and_delete(client, founder):
    client.force_login(founder)

    created = client.post(reverse("ops-work"), NEW, content_type="application/json")
    assert created.status_code == 201
    item = WorkItem.objects.get(slug=NEW["slug"])

    edited = client.patch(
        reverse("ops-work-item", args=[item.pk]),
        {"summary": "A booking site, now with gift vouchers."},
        content_type="application/json",
    )
    assert edited.status_code == 200
    item.refresh_from_db()
    assert item.summary == "A booking site, now with gift vouchers."

    removed = client.delete(reverse("ops-work-item", args=[item.pk]))
    assert removed.status_code in (200, 204)
    assert not WorkItem.objects.filter(pk=item.pk).exists()


# ── the consent gate, and operations telling the truth about it ─────────────


def test_consent_holds_even_when_published_is_ticked(client, founder):
    """
    The tick box is not permission, and the screen must not pretend it is.
    """
    client.force_login(founder)
    client.post(
        reverse("ops-work"),
        {**NEW, "is_published": True},
        content_type="application/json",
    )

    item = WorkItem.objects.get(slug=NEW["slug"])
    assert item.is_published is True
    # …and yet:
    assert item.permission_on_file is False
    assert item.is_publishable is False

    payload = client.get(reverse("ops-work")).json()
    row = next(r for r in payload["work"] if r["slug"] == NEW["slug"])
    assert row["is_published"] is True
    assert row["is_publishable"] is False
    assert row["needs_consent"] is True
    assert payload["awaiting_consent"] == SEEDED_HELD + 1
    assert payload["live_count"] == SEEDED_LIVE

    # The website's own payload is where this actually matters.
    public = client.get(reverse("public-work")).json()
    assert NEW["name"] not in str(public)


def test_recording_the_permission_releases_it(client, founder):
    client.force_login(founder)
    client.post(
        reverse("ops-work"),
        {**NEW, "is_published": True},
        content_type="application/json",
    )
    item = WorkItem.objects.get(slug=NEW["slug"])

    client.patch(
        reverse("ops-work-item", args=[item.pk]),
        {"permission_on_file": True},
        content_type="application/json",
    )

    payload = client.get(reverse("ops-work")).json()
    row = next(r for r in payload["work"] if r["slug"] == NEW["slug"])
    assert row["is_publishable"] is True
    assert payload["awaiting_consent"] == SEEDED_HELD
    assert payload["live_count"] == SEEDED_LIVE + 1

    public = client.get(reverse("public-work")).json()
    assert NEW["name"] in str(public)


def test_our_own_work_needs_nobody_s_permission(client, founder, platform):
    """
    The old page hid everything Genmars owns behind two clients' signatures.
    Whatever else changes, that must not come back.
    """
    client.force_login(founder)
    payload = client.get(reverse("ops-work")).json()
    row = next(r for r in payload["work"] if r["slug"] == platform.slug)
    assert row["needs_consent"] is False
    assert row["permission_on_file"] is False
    assert row["is_publishable"] is True
    assert payload["live_count"] == SEEDED_LIVE
    assert payload["awaiting_consent"] == SEEDED_HELD


def test_counts_are_not_simply_the_published_tick(client, founder, platform):
    """
    Falsifiability: with one publishable item and one held back, live_count
    and awaiting_consent must disagree with a naive count of is_published.
    """
    client.force_login(founder)
    client.post(
        reverse("ops-work"),
        {**NEW, "is_published": True},
        content_type="application/json",
    )

    payload = client.get(reverse("ops-work")).json()
    ticked = sum(1 for r in payload["work"] if r["is_published"])
    assert ticked == SEEDED_LIVE + 1
    assert payload["live_count"] == SEEDED_LIVE
    assert payload["awaiting_consent"] == 1


# ── the screen's other contracts ────────────────────────────────────────────


def test_the_categories_and_labels_the_screen_offers_come_from_the_server(
    client, engineer
):
    """
    The operations screen builds both dropdowns from this, and decides whether
    to enable the permission tick from `needs_consent`. Hard-coding either
    there would put the consent rule in two places.
    """
    client.force_login(engineer)
    payload = client.get(reverse("ops-work")).json()

    assert {c["key"] for c in payload["categories"]} == {
        "sites",
        "apps",
        "software",
        "design-systems",
        "tools",
        "integrations",
    }
    consenting = {l["key"] for l in payload["labels"] if l["needs_consent"]}
    assert consenting == set(WorkItem.NEEDS_CONSENT)
    assert consenting, "a label list where nothing needs consent tests nothing"


def test_a_duplicate_reference_is_refused_by_field(client, founder, platform):
    """The screen shows the message against the field, so there has to be one."""
    client.force_login(founder)
    response = client.post(
        reverse("ops-work"),
        {**NEW, "slug": platform.slug},
        content_type="application/json",
    )
    assert response.status_code == 400
    assert response.json().get("field") == "slug"


def test_a_missing_summary_is_refused_by_field(client, founder):
    client.force_login(founder)
    payload = {k: v for k, v in NEW.items() if k != "summary"}
    response = client.post(
        reverse("ops-work"), payload, content_type="application/json"
    )
    assert response.status_code == 400
    assert response.json().get("field") == "summary"


def test_publishing_is_recorded_and_an_ordinary_edit_is_not(
    client, founder, platform
):
    """
    The log records a change in what the public can see, not every keystroke —
    the same rule as a Doc. Fixing a typo in a live item is not an event;
    taking it off the site is.
    """
    client.force_login(founder)
    before = ActivityLog.objects.count()

    client.patch(
        reverse("ops-work-item", args=[platform.pk]),
        {"summary": "Branches, catalogue, stock and tills, in one place."},
        content_type="application/json",
    )
    assert ActivityLog.objects.count() == before

    client.patch(
        reverse("ops-work-item", args=[platform.pk]),
        {"is_published": False},
        content_type="application/json",
    )
    entry = ActivityLog.objects.order_by("-id").first()
    assert entry is not None
    assert entry.action == ActivityLog.Action.WORK_WITHDRAWN
    assert entry.detail["slug"] == platform.slug
    # A log line nobody can read is not a record. It has to name the thing.
    assert platform.name in entry.summary
