"""
Engineering delivery: the six definition-of-done gates, and blockers.

Charter 03 §II says partially done is not done, and gen-website publishes all
six conditions to anyone who visits /approach/. These tests are what make that
a promise the company can be held to rather than a paragraph.
"""

from __future__ import annotations

import pytest
from django.urls import reverse
from django.utils import timezone

from accounts.models import Organisation, User
from operations import services
from portal.models import Blocker, DeliveryGate, Enquiry, Order

pytestmark = pytest.mark.django_db

PASSWORD = "correct-horse-battery"


@pytest.fixture
def staff() -> User:
    return User.objects.create_user(
        email="ops@genmars.co.ke", password=PASSWORD, full_name="Ops",
        is_staff=True, email_verified_at=timezone.now(),
    )


@pytest.fixture
def order(staff) -> Order:
    org = Organisation.objects.create(name="Client Co")
    submitter = User.objects.create_user(
        email="client@example.com", password=PASSWORD, full_name="A Client",
        email_verified_at=timezone.now(),
    )
    enquiry = Enquiry.objects.create(
        organisation=org, submitted_by=submitter, problem="Reconciling by hand."
    )
    return services.convert_enquiry(
        enquiry=enquiry, actor=staff, title="Work", scope="Do the thing."
    )


@pytest.fixture
def signed_in(client, staff):
    client.force_login(staff)
    return client


# ── the gates exist at all ───────────────────────────────────────────────────


def test_an_order_gets_all_six_gates_on_creation(order):
    """
    Created WITH the order, not when someone remembers. A checklist you have to
    opt into is a standard that applies only to the orders somebody thought
    about.
    """
    assert order.gates.count() == 6
    assert set(order.gates.values_list("gate", flat=True)) == set(
        DeliveryGate.Gate.values
    )
    assert order.gates.filter(met_at__isnull=False).count() == 0


def test_the_wording_matches_what_the_site_publishes(order):
    """
    The labels here and gen-website's `definitionOfDone` are the same six
    sentences. If someone rewords one, this fails and they go and change both —
    which is the point, because the site is the public promise.
    """
    published = {
        "It works against realistic data, not the happy path only",
        "Automated tests cover the critical paths, and they pass in CI",
        "It is deployed to the target environment, not just to a branch",
        "Errors surface in monitoring rather than in a client phone call",
        "The deploy and rollback procedure is written down",
        "The client can perform the task the feature was built for, unaided",
    }
    assert set(order.gates.values_list("label", flat=True)) == published


def test_backfilling_is_idempotent(order):
    """Orders predating this feature need gates; running it twice must not
    give them twelve."""
    services.create_delivery_gates(order=order)
    services.create_delivery_gates(order=order)
    assert order.gates.count() == 6


# ── meeting a gate ───────────────────────────────────────────────────────────


def test_meeting_a_gate_requires_evidence(signed_in, order):
    """
    THE test in this file. A tick with nothing beside it is an opinion, and an
    auditable definition of done is the entire reason this exists.
    """
    gate = order.gates.first()
    response = signed_in.post(
        reverse("ops-gate", args=[order.reference, gate.pk]),
        {"met": True, "note": "   "},
        content_type="application/json",
    )
    assert response.status_code == 400
    assert response.json()["field"] == "note"
    gate.refresh_from_db()
    assert not gate.is_met


def test_meeting_a_gate_records_who_and_when(signed_in, order, staff):
    gate = order.gates.first()
    response = signed_in.post(
        reverse("ops-gate", args=[order.reference, gate.pk]),
        {"met": True, "note": "148 tests green in CI, run 33530696078."},
        content_type="application/json",
    )
    assert response.status_code == 200, response.json()
    gate.refresh_from_db()
    assert gate.is_met
    # Charter 01 §V — nothing ships without a named owner.
    assert gate.met_by == staff
    assert "148 tests" in gate.note


def test_a_gate_can_be_un_met_and_keeps_its_note(signed_in, order):
    """
    Work regresses — a test starts failing, a runbook goes stale. A checklist
    that could only ever be ticked is a ratchet that always reads complete.
    """
    gate = order.gates.first()
    url = reverse("ops-gate", args=[order.reference, gate.pk])
    signed_in.post(url, {"met": True, "note": "Was true in March."},
                   content_type="application/json")
    signed_in.post(url, {"met": False}, content_type="application/json")

    gate.refresh_from_db()
    assert not gate.is_met
    assert gate.met_by is None
    # The note survives, or un-ticking would erase why it was ever ticked.
    assert gate.note == "Was true in March."


def test_re_meeting_does_not_rewrite_the_original_timestamp(signed_in, order):
    gate = order.gates.first()
    url = reverse("ops-gate", args=[order.reference, gate.pk])
    signed_in.post(url, {"met": True, "note": "First."}, content_type="application/json")
    gate.refresh_from_db()
    first = gate.met_at

    signed_in.post(url, {"met": True, "note": "Same again."}, content_type="application/json")
    gate.refresh_from_db()
    assert gate.met_at == first


# ── blockers ─────────────────────────────────────────────────────────────────


def test_raising_and_clearing_a_blocker(signed_in, order, staff):
    created = signed_in.post(
        reverse("ops-blockers", args=[order.reference]),
        {"summary": "Waiting on M-Pesa API credentials",
         "detail": "Requested 2026-09-01.", "waiting_on": "client"},
        content_type="application/json",
    )
    assert created.status_code == 201, created.json()
    body = created.json()
    assert body["is_open"] is True
    assert body["waiting_on_label"] == "The client"
    assert body["raised_by"]["email"] == staff.email

    cleared = signed_in.post(
        reverse("ops-blocker", args=[order.reference, body["id"]]),
        {"resolution": "Credentials arrived."},
        content_type="application/json",
    )
    assert cleared.status_code == 200
    assert cleared.json()["is_open"] is False
    assert cleared.json()["resolution"] == "Credentials arrived."


def test_a_blocker_needs_a_summary(signed_in, order):
    response = signed_in.post(
        reverse("ops-blockers", args=[order.reference]),
        {"summary": "  ", "waiting_on": "us"},
        content_type="application/json",
    )
    assert response.status_code == 400


def test_blockers_are_not_visible_to_the_client(order, staff):
    """
    A blocker often names the client as the party being waited on. Publishing
    that unedited to their own dashboard would turn a working note into an
    accusation. The client sees progress notes, which are written for them.
    """
    from portal.selectors import export_payload

    Blocker.objects.create(
        order=order, summary="Client has not sent the data", waiting_on="client",
        raised_by=staff,
    )
    payload = export_payload(order.organisation.memberships.first().user) if order.organisation.memberships.exists() else export_payload(staff)
    assert "blocker" not in str(payload).lower()


# ── the board ────────────────────────────────────────────────────────────────


def test_the_board_puts_blocked_work_first(signed_in, order, staff):
    """
    A dashboard that opens on the newest thing is a list. Opening on the thing
    most at risk is the only reason to build one.
    """
    org2 = Organisation.objects.create(name="Second Co")
    calm = Order.objects.create(
        organisation=org2, reference="GM-2026-0999", title="Calm",
        scope="x", contact=staff, status=Order.Status.ACTIVE,
    )
    services.create_delivery_gates(order=calm)
    Blocker.objects.create(
        order=order, summary="Blocked", waiting_on="us", raised_by=staff
    )

    board = signed_in.get(reverse("ops-delivery")).json()
    assert board["orders"][0]["reference"] == order.reference
    assert board["orders"][0]["blockers_open"] == 1
    assert board["counts"]["open_blockers"] == 1
    assert board["counts"]["active_orders"] == 2


def test_fully_met_counts_only_orders_with_every_gate_met(signed_in, order, staff):
    board = signed_in.get(reverse("ops-delivery")).json()
    assert board["counts"]["fully_met"] == 0

    for gate in order.gates.all():
        services.set_gate(gate=gate, actor=staff, met=True, note="done")

    board = signed_in.get(reverse("ops-delivery")).json()
    assert board["counts"]["fully_met"] == 1


def test_no_client_account_reaches_the_delivery_endpoints(client, order, staff):
    """The same lockout as every other operations route."""
    outsider = User.objects.create_user(
        email="outsider@example.com", password=PASSWORD, full_name="Outsider",
        email_verified_at=timezone.now(),
    )
    client.force_login(outsider)
    gate = order.gates.first()
    for url in [
        reverse("ops-delivery"),
        reverse("ops-delivery-backfill"),
        reverse("ops-gate", args=[order.reference, gate.pk]),
        reverse("ops-blockers", args=[order.reference]),
    ]:
        assert client.get(url).status_code == 403, url
        assert client.post(url, content_type="application/json").status_code == 403, url
