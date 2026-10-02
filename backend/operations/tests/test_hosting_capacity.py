"""
Selling hosting off a machine that has room for it.

═══════════════════════════════════════════════════════════════════════════════
THE ONE THAT MATTERS: test_a_node_cannot_be_sold_past_what_it_has.

genmars.co.ke publishes managed hosting with storage bounded per tier, and the
reason the bound is on the page is the exclusion that has been in the
catalogue since it was written — "an unlimited resource commitment inside a
fixed monthly fee is a promise that gets quietly broken."

A bound published and never counted IS that promise. The shared host is 75 GB
with the Genmars applications already on it; four Application plans at 20 GB
each fit on the page and do not fit on the disk, and the way that is otherwise
discovered is a client's database refusing to write.
═══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from accounts.models import Organisation, User
from operations import selectors, services
from portal.models import (
    ActivityLog,
    HostingArrangement,
    HostingNode,
    Service,
    ServiceTier,
)

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
def node() -> HostingNode:
    """
    The shared host, to scale: 75 GB with our own systems on it already.

    The numbers are the real ones deliberately. A fixture with round
    hundreds would pass against arithmetic that cannot actually seat the
    tiers we publish.
    """
    return HostingNode.objects.create(
        name="hetzner-shared-1",
        provider="Hetzner",
        vcpus=4,
        memory_mb=7500,
        storage_gb=75,
        reserved_storage_gb=55,
    )


@pytest.fixture
def plans() -> dict[str, ServiceTier]:
    service = Service.objects.create(
        name="Managed hosting", slug="hosting", summary="…", price_unit="per year"
    )
    return {
        "site": ServiceTier.objects.create(
            service=service, slug="site", name="Site",
            price_kes=Decimal("15000"), published_price_kes=Decimal("15000"),
            position=1,
        ),
        "application": ServiceTier.objects.create(
            service=service, slug="application", name="Application",
            price_kes=Decimal("48000"), published_price_kes=Decimal("48000"),
            position=2,
        ),
        "dedicated": ServiceTier.objects.create(
            service=service, slug="dedicated", name="Dedicated",
            price_kes=Decimal("150000"), published_price_kes=Decimal("150000"),
            is_from=True, position=3,
        ),
    }


def place(node, staff, spa, gb, identifier="a.example", **extra):
    return services.record_hosting(
        organisation=spa,
        actor=staff,
        values={
            "kind": HostingArrangement.Kind.HOSTING,
            "identifier": identifier,
            "node": node,
            "allocated_storage_gb": gb,
            **extra,
        },
    )


# ── the capacity arithmetic ──────────────────────────────────────────────────


def test_a_node_knows_what_is_left_after_our_own_systems(node):
    """
    75 GB of disk is not 75 GB of sellable disk.

    The portal, the ops dashboard, the website, the Business Platform and
    their databases are on this machine. Counting capacity as "total minus
    what clients hold" would count our own footprint as free and sell it
    twice.
    """
    assert node.sellable_storage_gb == 20
    assert node.committed_storage_gb() == 0
    assert node.free_storage_gb() == 20


def test_a_node_cannot_be_sold_past_what_it_has(node, staff, spa):
    place(node, staff, spa, 15, "first.example")

    with pytest.raises(services.OperationsError) as refusal:
        place(node, staff, spa, 20, "second.example")

    # The numbers are in the message, all three of them. "Not enough space"
    # sends somebody to a terminal to work out which of the three it is.
    message = str(refusal.value)
    assert "5 GB left" in message
    assert "needs 20 GB" in message
    assert "55 GB is reserved" in message
    assert "15 GB is already committed" in message

    assert HostingArrangement.objects.filter(identifier="second.example").count() == 0


def test_the_refusal_names_the_field_the_answer_goes_in(node, staff, spa):
    place(node, staff, spa, 20, "first.example")
    with pytest.raises(services.OperationsError) as refusal:
        place(node, staff, spa, 1, "second.example")
    assert refusal.value.field == "despite_full"


def test_an_oversubscription_can_be_recorded_but_costs_a_sentence(node, staff, spa):
    """
    The override is why the guard holds rather than a hole in it.

    A node genuinely does get oversubscribed — a client is migrated on the
    day before the bigger box arrives. Refusing to record that does not
    prevent it; it prevents the RECORD of it, and leaves somebody editing the
    row directly with no trace at all.
    """
    place(node, staff, spa, 20, "first.example")

    arrangement = place(
        node, staff, spa, 10, "second.example",
        despite_full="Migrating off Truehost today; CPX41 arrives Friday.",
    )

    assert arrangement.allocated_storage_gb == 10
    assert node.free_storage_gb() == -10

    entry = ActivityLog.objects.get(
        action=ActivityLog.Action.HOSTING_RECORDED, subject="second.example"
    )
    assert entry.detail["despite_full"].startswith("Migrating off Truehost")


def test_a_blank_reason_is_not_a_reason(node, staff, spa):
    place(node, staff, spa, 20, "first.example")
    with pytest.raises(services.OperationsError):
        place(node, staff, spa, 5, "second.example", despite_full="   ")


def test_nothing_is_checked_where_nothing_is_consumed(node, staff, spa):
    """
    A domain renewal and a mailbox at Zoho occupy none of our disk.

    If the guard fired on them it would be unusable: most arrangements are
    not hosting, and most hosting pre-dates having a node recorded at all.
    """
    place(node, staff, spa, 20, "first.example")

    services.record_hosting(
        organisation=spa, actor=staff,
        values={
            "kind": HostingArrangement.Kind.DOMAIN,
            "identifier": "clipsserenityspa.co.ke",
            "provider": "Truehost",
        },
    )
    assert HostingArrangement.objects.filter(kind="domain").count() == 1


def test_retiring_an_arrangement_gives_its_space_back(node, staff, spa):
    first = place(node, staff, spa, 20, "first.example")
    services.retire_hosting(arrangement=first, actor=staff, reason="Client left.")

    assert node.free_storage_gb() == 20
    # And the next sale now fits, which is the point of counting live rows
    # only rather than every row that ever existed.
    place(node, staff, spa, 20, "second.example")


def test_growing_a_plan_is_not_charged_for_the_space_it_already_holds(
    node, staff, spa
):
    """
    5 GB to 10 GB needs 5 GB free, not 15.

    Counting the arrangement's own current allocation inside the committed
    total would refuse the most ordinary upgrade there is on a node with room
    for it.
    """
    arrangement = place(node, staff, spa, 18, "first.example")
    services.update_hosting(
        arrangement=arrangement, actor=staff, values={"allocated_storage_gb": 20}
    )
    arrangement.refresh_from_db()
    assert arrangement.allocated_storage_gb == 20


def test_an_upgrade_past_the_node_is_still_refused(node, staff, spa):
    arrangement = place(node, staff, spa, 18, "first.example")
    with pytest.raises(services.OperationsError):
        services.update_hosting(
            arrangement=arrangement, actor=staff, values={"allocated_storage_gb": 21}
        )


# ── the price list, one step down ────────────────────────────────────────────


def test_a_plan_sets_the_charge_so_nobody_re_keys_it(node, staff, spa, plans):
    arrangement = place(
        node, staff, spa, 20, "first.example", plan=plans["application"]
    )
    assert arrangement.annual_charge_kes == Decimal("48000")
    assert arrangement.charge_matches_plan is True


def test_a_from_tier_never_fills_in_a_price_it_has_not_quoted(
    node, staff, spa, plans
):
    """
    Dedicated is published as "from KES 150,000" — a floor, not a price.

    Defaulting the charge to the floor would turn every Dedicated client into
    one paying the minimum, which is a quote nobody gave.
    """
    arrangement = place(
        node, staff, spa, 20, "first.example", plan=plans["dedicated"]
    )
    assert arrangement.annual_charge_kes is None


def test_a_client_paying_off_the_price_list_is_visible(node, staff, spa, plans):
    arrangement = place(
        node, staff, spa, 20, "first.example",
        plan=plans["application"], annual_charge_kes=Decimal("30000"),
    )
    assert arrangement.charge_matches_plan is False


def test_above_the_floor_of_a_from_tier_is_correct_and_below_it_is_not(
    node, staff, spa, plans
):
    high = place(
        node, staff, spa, 10, "high.example",
        plan=plans["dedicated"], annual_charge_kes=Decimal("200000"),
    )
    low = place(
        node, staff, spa, 10, "low.example",
        plan=plans["dedicated"], annual_charge_kes=Decimal("90000"),
        despite_full="Second box, recorded against this one until it is set up.",
    )
    assert high.charge_matches_plan is True
    assert low.charge_matches_plan is False


def test_unanswerable_is_not_the_same_as_correct(node, staff, spa):
    """
    No plan means None, and a screen must not render that as a tick.

    Most arrangements pre-date the price list; reporting them as "matches"
    would make the whole column meaningless.
    """
    arrangement = place(node, staff, spa, 10, "first.example")
    assert arrangement.charge_matches_plan is None


# ── the node itself ──────────────────────────────────────────────────────────


def test_shrinking_a_node_under_what_is_on_it_is_allowed_and_logged(
    node, staff, spa
):
    """
    The disk is whatever the disk is.

    Refusing a measurement would be refusing reality. What must not happen is
    it passing unnoticed — by the next sale, several clients are affected.
    """
    place(node, staff, spa, 20, "first.example")
    services.update_hosting_node(
        node=node, actor=staff, values={"reserved_storage_gb": 65}
    )

    node.refresh_from_db()
    assert node.free_storage_gb() == -10

    entry = ActivityLog.objects.filter(
        action=ActivityLog.Action.NODE_CHANGED
    ).latest("created_at")
    assert "OVERSUBSCRIBED by 10 GB" in entry.summary
    assert entry.detail["oversubscribed_gb"] == 10


def test_two_nodes_cannot_share_a_name(staff, node):
    with pytest.raises(services.OperationsError) as refusal:
        services.record_hosting_node(
            actor=staff, values={"name": "Hetzner-Shared-1", "storage_gb": 40}
        )
    assert refusal.value.field == "name"


def test_a_reservation_above_the_disk_reads_as_nothing_sellable(staff):
    """
    Clamped at zero rather than going negative.

    A negative capacity would make every comparison downstream read
    backwards, and quietly turn a refusal into a sale.
    """
    node = services.record_hosting_node(
        actor=staff,
        values={"name": "miscounted", "storage_gb": 40, "reserved_storage_gb": 50},
    )
    assert node.sellable_storage_gb == 0


# ── the screen ───────────────────────────────────────────────────────────────


def test_the_node_list_counts_every_node_in_one_pass(
    client, staff, spa, node, django_assert_num_queries
):
    """
    The annotation is the point, so it is pinned.

    This is the screen somebody opens precisely when deciding whether a sale
    fits, and a query per node makes it the slow one.
    """
    place(node, staff, spa, 5, "first.example")
    place(node, staff, spa, 5, "second.example")
    for n in range(4):
        HostingNode.objects.create(name=f"node-{n}", storage_gb=40)

    with django_assert_num_queries(1):
        rows = list(selectors.hosting_nodes())
        committed = [(r.name, int(r.committed_gb), int(r.live_count)) for r in rows]

    assert ("hetzner-shared-1", 10, 2) in committed
    # An empty node appears with zero rather than dropping out of the list —
    # an empty node is the answer to "where does this go".
    assert ("node-0", 0, 0) in committed


def test_an_empty_node_is_not_missing_from_the_screen(client, staff, node):
    HostingNode.objects.create(name="spare", storage_gb=40, reserved_storage_gb=5)
    client.force_login(staff)

    body = client.get(reverse("ops-hosting-nodes")).json()
    names = {n["name"]: n for n in body["nodes"]}

    assert names["spare"]["free_storage_gb"] == 35
    assert names["spare"]["live_arrangements"] == 0


def test_a_client_cannot_read_the_node_list(client, spa):
    outsider = User.objects.create_user(
        email="client@example.com",
        password=PASSWORD,
        full_name="A Client",
        email_verified_at=timezone.now(),
    )
    client.force_login(outsider)
    assert client.get(reverse("ops-hosting-nodes")).status_code == 403


def test_the_client_page_offers_the_pickers_it_needs(client, staff, spa, node, plans):
    client.force_login(staff)
    body = client.get(reverse("ops-client", args=[spa.id])).json()

    assert [n["label"] for n in body["hosting_nodes"]] == ["hetzner-shared-1"]
    assert body["hosting_nodes"][0]["free_storage_gb"] == 20
    assert [p["label"] for p in body["hosting_plans"]] == [
        "Site", "Application", "Dedicated",
    ]


def test_a_retired_node_is_not_offered_for_a_new_sale(client, staff, spa, node):
    services.update_hosting_node(node=node, actor=staff, values={"is_active": False})
    client.force_login(staff)

    body = client.get(reverse("ops-client", args=[spa.id])).json()
    assert body["hosting_nodes"] == []

    refused = client.post(
        reverse("ops-client-hosting", args=[spa.id]),
        data={
            "kind": "hosting",
            "identifier": "late.example",
            "node": node.id,
            "allocated_storage_gb": 5,
        },
        content_type="application/json",
    )
    assert refused.status_code == 400


def test_the_capacity_refusal_reaches_the_client_as_a_refusal_not_a_crash(
    client, staff, spa, node
):
    place(node, staff, spa, 20, "first.example")
    client.force_login(staff)

    response = client.post(
        reverse("ops-client-hosting", args=[spa.id]),
        data={
            "kind": "hosting",
            "identifier": "second.example",
            "node": node.id,
            "allocated_storage_gb": 10,
        },
        content_type="application/json",
    )
    assert response.status_code == 400
    assert "0 GB left to sell" in str(response.json())
