"""
What a client can see about the work still running for them.

═══════════════════════════════════════════════════════════════════════════════
TWO TESTS CARRY THIS FILE, AND THEY PULL IN OPPOSITE DIRECTIONS.

test_a_client_can_see_which_of_their_accounts_we_hold — Charter 05 §VIII. A
promise not to hold domains hostage is worth little if they cannot see which
of their accounts is in our name.

test_the_dashboard_never_shows_what_a_domain_costs_us — the same screen must
not leak our margin. Openness about what we HOLD is not openness about what we
PAY, and confusing the two turns every renewal into a negotiation about markup.
═══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from accounts.models import Membership, Organisation, User
from portal.models import HostingArrangement, Order

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
def hosting(spa) -> HostingArrangement:
    return HostingArrangement.objects.create(
        organisation=spa,
        kind=HostingArrangement.Kind.DOMAIN,
        identifier="clipsserenityspa.co.ke",
        provider="Truehost",
        account_holder=HostingArrangement.Holder.GENMARS,
        renews_on=timezone.localdate() + timedelta(days=40),
        annual_cost_kes=Decimal("1200.00"),
        annual_charge_kes=Decimal("2500.00"),
        notes="Card on file is the founder's. Move it.",
    )


# ── what is still running ────────────────────────────────────────────────────


def test_ongoing_work_is_separated_from_work_that_finished(client, staff, spa, owner):
    """
    Once past work started being recorded here, a client's list became things
    finished years ago mixed with things still live, in one column. "Is this
    retainer still running" was answerable only by reading every row.
    """
    Order.objects.create(
        organisation=spa, reference="GM-2025-0001", title="Spa website",
        contact=staff, scope="Built it.", status=Order.Status.DELIVERED,
        kind=Order.Kind.PROJECT, completed_on="2025-05-20",
        recorded_retrospectively=True,
    )
    Order.objects.create(
        organisation=spa, reference="GM-2025-0002", title="Monthly upkeep",
        contact=staff, scope="Updates and backups.", status=Order.Status.ACTIVE,
        kind=Order.Kind.RETAINER, started_on="2025-06-01",
    )

    client.force_login(owner)
    body = client.get(reverse("dashboard")).json()

    assert [o["reference"] for o in body["ongoing"]] == ["GM-2025-0002"]
    assert body["ongoing"][0]["kind_label"] == "Retainer"


def test_a_client_sees_the_shape_and_dates_of_their_work(client, staff, spa, owner):
    Order.objects.create(
        organisation=spa, reference="GM-2025-0002", title="Monthly upkeep",
        contact=staff, scope="Updates and backups.", status=Order.Status.ACTIVE,
        kind=Order.Kind.RETAINER, started_on="2025-06-01",
    )
    client.force_login(owner)
    row = client.get(reverse("dashboard")).json()["ongoing"][0]

    assert row["kind"] == "retainer"
    assert row["started_on"] == "2025-06-01"
    assert row["completed_on"] is None


# ── what we run for them ─────────────────────────────────────────────────────


def test_a_client_can_see_which_of_their_accounts_we_hold(client, spa, owner, hosting):
    """
    ═══════════════════════════════════════════════════════════════════════════
    CHARTER 05 §VIII, AS SOMETHING THEY CAN CHECK RATHER THAN TAKE ON TRUST.

    A domain in Genmars' name is one they cannot take with them without asking
    us. Until now the only place that fact existed was an operations screen
    they have no access to — and it is exactly what gets discovered at the
    worst moment.
    ═══════════════════════════════════════════════════════════════════════════
    """
    client.force_login(owner)
    row = client.get(reverse("dashboard")).json()["hosting"][0]

    assert row["identifier"] == "clipsserenityspa.co.ke"
    assert row["holder_label"] == "Genmars"
    # As a boolean too, so the page can lead with the fact rather than make
    # the reader decode a label.
    assert row["in_our_name"] is True
    assert row["renews_on"] is not None
    assert row["days_until_renewal"] == 40


def test_the_dashboard_never_shows_what_a_domain_costs_us(client, spa, owner, hosting):
    """
    ═══════════════════════════════════════════════════════════════════════════
    Openness about what we HOLD is not openness about what we PAY.

    What they pay is theirs to see. What it costs us is our margin, and
    publishing it turns every renewal into a negotiation about markup rather
    than about the service.
    ═══════════════════════════════════════════════════════════════════════════
    """
    client.force_login(owner)
    response = client.get(reverse("dashboard"))
    row = response.json()["hosting"][0]

    assert "annual_cost_kes" not in row
    assert "1200" not in response.content.decode()
    # What they pay IS shown.
    assert row["annual_charge_kes"] == "2500.00"


def test_internal_notes_never_reach_the_client(client, spa, owner, hosting):
    """Same category as the contact log: written honestly because nobody
    outside Genmars reads them."""
    client.force_login(owner)
    body = client.get(reverse("dashboard")).content.decode()
    assert "Card on file" not in body
    assert "notes" not in body


def test_a_retired_arrangement_stops_showing(client, spa, owner, hosting):
    """This answers "what is running now". The history stays in operations."""
    hosting.retired_at = timezone.now()
    hosting.save(update_fields=["retired_at"])

    client.force_login(owner)
    assert client.get(reverse("dashboard")).json()["hosting"] == []


def test_one_client_never_sees_another_clients_hosting(client, spa, owner):
    """
    Scoped through membership in portal/selectors.py like every other client
    read. A domain name is commercially revealing on its own.
    """
    other = Organisation.objects.create(name="Somebody Else Ltd")
    HostingArrangement.objects.create(
        organisation=other, kind=HostingArrangement.Kind.DOMAIN,
        identifier="somebodyelse.co.ke",
    )

    client.force_login(owner)
    body = client.get(reverse("dashboard")).content.decode()
    assert "somebodyelse.co.ke" not in body


def test_someone_with_no_membership_sees_an_empty_dashboard(client):
    """The ordinary state for a fresh signup, and it must not be an error."""
    stranger = User.objects.create_user(
        email="nobody@example.com", password=PASSWORD, email_verified_at=timezone.now()
    )
    client.force_login(stranger)
    body = client.get(reverse("dashboard")).json()

    assert body["hosting"] == []
    assert body["ongoing"] == []
