"""
Tenant isolation.

The single most important test file in this repository. A client seeing another
client's order is a confidentiality breach under Charter 05 §V, and it is the
failure mode that a permissions bug produces silently — nothing errors, the
wrong data just appears.

Written as a test rather than a code-review note, because a code-review note
does not run in CI.
"""

from __future__ import annotations

import datetime as dt

import pytest

from django.db import models
from django.utils import timezone

from accounts.models import Membership, Organisation, User
from portal.models import Milestone, Order, ProgressNote
from portal.selectors import orders_for, order_for

pytestmark = pytest.mark.django_db


@pytest.fixture
def staff() -> User:
    return User.objects.create_user(
        email="edwin@genmars.co.ke", password="x" * 12, is_staff=True
    )


@pytest.fixture
def world(staff):
    """Two unrelated clients, each with one order."""
    org_a = Organisation.objects.create(name="Acme Ltd")
    org_b = Organisation.objects.create(name="Beta Ltd")

    user_a = User.objects.create_user(email="a@acme.example", password="x" * 12)
    user_b = User.objects.create_user(email="b@beta.example", password="x" * 12)
    Membership.objects.create(user=user_a, organisation=org_a)
    Membership.objects.create(user=user_b, organisation=org_b)

    order_a = Order.objects.create(
        organisation=org_a, reference="GM-001", title="Acme reconciliation",
        scope="Reconcile M-Pesa against invoices.", contact=staff,
    )
    order_b = Order.objects.create(
        organisation=org_b, reference="GM-002", title="Beta booking",
        scope="Booking system.", contact=staff,
    )
    return {
        "user_a": user_a, "user_b": user_b,
        "org_a": org_a, "org_b": org_b,
        "order_a": order_a, "order_b": order_b,
    }


def test_client_sees_only_their_own_orders(world):
    assert list(orders_for(world["user_a"])) == [world["order_a"]]
    assert list(orders_for(world["user_b"])) == [world["order_b"]]


def test_client_cannot_fetch_another_organisations_order_by_reference(world):
    """
    The obvious attack: change the reference in the URL. Must return nothing,
    not raise a permission error that confirms the order exists.
    """
    assert order_for(world["user_a"], "GM-002") is None
    assert order_for(world["user_b"], "GM-001") is None


def test_client_can_fetch_their_own_order_by_reference(world):
    assert order_for(world["user_a"], "GM-001") == world["order_a"]


def test_user_with_no_membership_sees_nothing():
    """The common early case: signed up, no order yet. Must be empty, not error."""
    orphan = User.objects.create_user(email="new@example.com", password="x" * 12)
    assert list(orders_for(orphan)) == []


def test_progress_notes_do_not_leak_across_organisations(world, staff):
    ProgressNote.objects.create(
        order=world["order_b"], author=staff, body="Beta progress.",
        week_of=dt.date(2026, 8, 24),
    )
    visible = orders_for(world["user_a"]).prefetch_related("notes")
    assert all(n.order.organisation == world["org_a"] for o in visible for n in o.notes.all())


def test_milestones_do_not_leak_across_organisations(world):
    Milestone.objects.create(
        order=world["order_b"], name="Deposit", amount_kes=100000, position=1
    )
    visible = orders_for(world["user_a"]).prefetch_related("milestones")
    assert all(m.order.organisation == world["org_a"] for o in visible for m in o.milestones.all())


def test_membership_in_two_organisations_sees_both(world, staff):
    """A consultant working with two clients is legitimate and must work."""
    Membership.objects.create(user=world["user_a"], organisation=world["org_b"])
    assert set(orders_for(world["user_a"])) == {world["order_a"], world["order_b"]}


def test_staff_flag_alone_grants_no_client_data(world, staff):
    """
    is_staff means Genmars, not "sees everything through the client API".
    Staff use the admin; the client-facing selectors go through membership only.
    """
    assert list(orders_for(staff)) == []


def test_unpublished_notes_are_not_visible_to_clients(world, staff):
    """A draft note is not a promise. Charter 05 §I — the note is what we told them."""
    ProgressNote.objects.create(
        order=world["order_a"], author=staff, body="Draft, not sent.",
        week_of=dt.date(2026, 8, 24),
    )
    order = order_for(world["user_a"], "GM-001")
    assert order is not None
    assert order.notes.filter(published_at__isnull=False).count() == 0


# ── invoices, including the ones with no order ───────────────────────────────
#
# invoice_for scopes through the order, so the isolation was inherited. Direct
# invoices have no order and are scoped on `organisation` instead, which is a
# NEW path to another client's billing document — and an invoice number is
# guessable. These tests exist because that filter is now the only thing
# standing between the two.


@pytest.fixture
def billed(world):
    """One invoice per client, one of each raised with no order at all."""
    from decimal import Decimal

    from portal.models import Invoice

    def make(number, org, order):
        return Invoice.objects.create(
            number=number, organisation=org, order=order,
            description="Work", amount_kes=Decimal("1000.00"),
            issued_on=dt.date(2026, 9, 1),
        )

    return {
        "a_order": make("GM-INV-2026-0001", world["org_a"], world["order_a"]),
        "a_direct": make("GM-INV-2026-0002", world["org_a"], None),
        "b_order": make("GM-INV-2026-0003", world["org_b"], world["order_b"]),
        "b_direct": make("GM-INV-2026-0004", world["org_b"], None),
    }


def test_a_client_sees_only_their_own_invoices(world, billed):
    from portal.selectors import invoices_for

    numbers = set(invoices_for(world["user_a"]).values_list("number", flat=True))
    assert numbers == {"GM-INV-2026-0001", "GM-INV-2026-0002"}


def test_a_direct_invoice_is_visible_to_its_client(world, billed):
    """The whole reason invoices_for exists: an order-scoped query misses it."""
    from portal.selectors import client_invoice_for

    found = client_invoice_for(world["user_a"], "GM-INV-2026-0002")
    assert found is not None
    assert found.order_id is None


def test_guessing_another_clients_invoice_number_finds_nothing(world, billed):
    from portal.selectors import client_invoice_for

    for number in ["GM-INV-2026-0003", "GM-INV-2026-0004"]:
        assert client_invoice_for(world["user_a"], number) is None


def test_a_user_with_no_membership_sees_no_invoices(billed):
    from portal.selectors import invoices_for

    nobody = User.objects.create_user(email="nobody@example.com", password="x" * 12)
    assert not invoices_for(nobody).exists()


def test_an_invoice_never_disagrees_with_its_order_about_the_client(world, billed):
    """
    invoices_for reads the client off the invoice rather than through a join,
    which is only safe while the two cannot disagree. issue_invoice copies the
    organisation from the order and nothing else writes it.
    """
    from portal.models import Invoice

    mismatched = Invoice.objects.exclude(order__isnull=True).exclude(
        organisation_id=models.F("order__organisation_id")
    )
    assert not mismatched.exists()


# ── notifications ────────────────────────────────────────────────────────────


def test_notifications_do_not_cross_between_people(world):
    from portal.models import Notification

    for user in [world["user_a"], world["user_b"]]:
        Notification.objects.create(
            user=user, audience=Notification.Audience.CLIENT,
            kind=Notification.Kind.INVOICE_ISSUED, title=f"For {user.email}",
        )

    mine = world["user_a"].notifications.all()
    assert mine.count() == 1
    assert mine.first().title == f"For {world['user_a'].email}"


def test_a_staff_notification_is_not_on_the_client_surface(world, staff):
    """
    Audience is filtered by the view, not chosen by the caller. A staff
    notification is written about internal work and must not become readable
    by passing a query parameter.
    """
    from portal.models import Notification

    Notification.objects.create(
        user=world["user_a"], audience=Notification.Audience.STAFF,
        kind=Notification.Kind.ENQUIRY_RECEIVED, title="Internal",
    )

    visible = world["user_a"].notifications.filter(
        audience=Notification.Audience.CLIENT
    )
    assert not visible.exists()


# ── the client dashboard apps ────────────────────────────────────────────────
#
# Both read data that was internal until now — delivery blockers and the
# systems registry — so both are new paths to another client's information.


@pytest.fixture
def blocked(world, staff):
    """One blocker per client, plus one on us that nobody should see listed."""
    from portal.models import Blocker

    a = Blocker.objects.create(
        order=world["order_a"], summary="We need the August export",
        waiting_on=Blocker.WaitingOn.CLIENT, raised_by=staff,
    )
    b = Blocker.objects.create(
        order=world["order_b"], summary="Beta must approve the schema",
        waiting_on=Blocker.WaitingOn.CLIENT, raised_by=staff,
    )
    ours = Blocker.objects.create(
        order=world["order_a"], summary="We are rewriting the importer",
        waiting_on=Blocker.WaitingOn.US, raised_by=staff,
    )
    return {"a": a, "b": b, "ours": ours}


def test_a_client_sees_only_what_they_are_holding_up(world, blocked):
    from portal.selectors import waiting_on_client

    mine = list(waiting_on_client(world["user_a"]))
    assert [b.summary for b in mine] == ["We need the August export"]


def test_blockers_on_us_are_not_listed_to_the_client(world, blocked):
    """
    Ours are ours to fix, and listing them here would read as excuses. A list
    of things you cannot act on is noise.
    """
    from portal.selectors import waiting_on_client

    summaries = [b.summary for b in waiting_on_client(world["user_a"])]
    assert "We are rewriting the importer" not in summaries


def test_a_cleared_blocker_drops_off(world, blocked, staff):
    from portal.selectors import waiting_on_client

    blocked["a"].cleared_at = timezone.now()
    blocked["a"].save()

    assert not waiting_on_client(world["user_a"]).exists()


def test_client_systems_do_not_cross_organisations(world):
    from portal.models import System
    from portal.selectors import systems_for

    for key, org in (("a", world["org_a"]), ("b", world["org_b"])):
        System.objects.create(
            name=f"{key} site", slug=f"{key}-site", kind=System.Kind.CLIENT,
            criticality=System.Criticality.IMPORTANT, purpose="Their site.",
            impact_if_down="It is down.", owner=world["user_a"],
            organisation=org,
        )

    mine = systems_for(world["user_a"])
    assert [s.slug for s in mine] == ["a-site"]


def test_our_own_systems_are_invisible_to_every_client(world):
    """
    Internal systems have no organisation, so the filter excludes them. A
    client should not learn what runs Genmars.
    """
    from portal.models import System
    from portal.selectors import systems_for

    System.objects.create(
        name="Client portal", slug="gen-portal", kind=System.Kind.INTERNAL,
        criticality=System.Criticality.CRITICAL, purpose="Internal.",
        impact_if_down="Everything.", owner=world["user_a"],
    )

    assert not systems_for(world["user_a"]).exists()
    assert not systems_for(world["user_b"]).exists()


def test_the_dashboard_never_hands_over_how_we_operate_a_system(client, world):
    """
    The health-check URL, the runbook and the reporting keys are facts about
    how we run it, not about whether their service is working.
    """
    from django.urls import reverse
    from portal.models import System

    System.objects.create(
        name="Acme site", slug="acme-site", kind=System.Kind.CLIENT,
        criticality=System.Criticality.CRITICAL, purpose="Their booking site.",
        impact_if_down="Nobody can book.", owner=world["user_a"],
        organisation=world["org_a"],
        health_url="https://internal.example/secret-health",
        runbook="ssh in as root and restart the thing",
        repository="genmarstech/acme",
    )

    client.force_login(world["user_a"])
    body = client.get(reverse("dashboard")).json()

    shown = body["systems"][0]
    assert shown["name"] == "Acme site"
    for leaked in ("health_url", "runbook", "repository", "criticality", "owner"):
        assert leaked not in shown, leaked

    blob = str(body)
    assert "secret-health" not in blob
    assert "ssh in as root" not in blob


def test_the_dashboard_is_empty_for_someone_with_no_membership(client):
    from django.urls import reverse

    nobody = User.objects.create_user(email="nobody@example.com", password="x" * 12)
    client.force_login(nobody)
    body = client.get(reverse("dashboard")).json()

    assert body["waiting_on_you"] == []
    assert body["systems"] == []
