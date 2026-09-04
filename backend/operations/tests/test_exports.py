"""
Reports out, as CSV.

═══════════════════════════════════════════════════════════════════════════════
THE TEST THAT MATTERS IS test_a_cell_cannot_smuggle_a_formula.

Client-supplied text lands in these files — a conversation summary, an order
scope. A cell beginning = + - or @ is treated as a FORMULA by Excel, Numbers
and Sheets, so a summary that happens to start with a dash becomes a broken
formula at best and something that runs at worst.
═══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import csv
from datetime import timedelta
from decimal import Decimal
from io import StringIO

import pytest
from django.urls import reverse
from django.utils import timezone

from accounts.models import Membership, Organisation, User
from operations import services
from portal.models import ActivityLog, ContactLogEntry, Contract, Invoice, Order, Shift

pytestmark = pytest.mark.django_db

PASSWORD = "correct-horse-battery"


@pytest.fixture
def staff() -> User:
    return User.objects.create_user(
        email="ops@genmars.co.ke", password=PASSWORD, full_name="Ops Person",
        is_staff=True, staff_role=User.StaffRole.FOUNDER,
        email_verified_at=timezone.now(),
    )


@pytest.fixture
def spa() -> Organisation:
    return Organisation.objects.create(name="Clips Serenity Spa")


def _pull(client, report, **params):
    response = client.post(
        reverse("ops-reports"), {"report": report, **params},
        content_type="application/json",
    )
    assert response.status_code == 200, response.content[:300]
    return response


def _rows(response):
    return list(csv.reader(StringIO(response.content.decode())))


def _billable(spa, staff, reference="GM-2026-0001") -> Order:
    order = Order.objects.create(
        organisation=spa, reference=reference, title="Spa website",
        contact=staff, scope="Build it.",
    )
    Contract.objects.create(
        order=order, version=1, title="Statement of work", scope="Build it.",
        total_kes=Decimal("100000.00"), status=Contract.Status.SIGNED,
        issued_by=staff, signed_on=timezone.localdate(), signed_by_name="The owner",
    )
    return order


# ── the shape of a usable spreadsheet ────────────────────────────────────────


def test_amounts_are_numbers_a_spreadsheet_can_add_up(client, staff, spa):
    """
    "KES 45,000.00" arrives as text and cannot be summed. This is the single
    commonest way an exported report becomes useless.
    """
    order = _billable(spa, staff)
    services.issue_invoice(
        order=order, actor=staff, description="Deposit", amount_kes=Decimal("45000.00")
    )

    client.force_login(staff)
    rows = _rows(_pull(client, "invoices"))
    header, row = rows[0], rows[1]

    amount = row[header.index("Amount KES")]
    assert amount == "45000.00"
    assert float(amount) == 45000.0


def test_nothing_paid_is_blank_rather_than_zero(client, staff, spa):
    """
    Zero is a fact — an invoice for nothing. A blank is the absence of one, and
    a report writing zero for both makes the difference unrecoverable at the
    point somebody totals the column.
    """
    order = _billable(spa, staff)
    services.issue_invoice(
        order=order, actor=staff, description="Deposit", amount_kes=Decimal("45000.00")
    )

    client.force_login(staff)
    rows = _rows(_pull(client, "invoices"))
    assert rows[1][rows[0].index("Paid KES")] == ""


def test_a_cell_cannot_smuggle_a_formula(client, staff, spa):
    """
    ═══════════════════════════════════════════════════════════════════════════
    Client-supplied text lands in these files.
    ═══════════════════════════════════════════════════════════════════════════
    """
    ContactLogEntry.objects.create(
        organisation=spa, channel="call", direction="inbound",
        summary="=cmd|'/c calc'!A0",
    )
    client.force_login(staff)
    rows = _rows(_pull(client, "conversations"))

    summary = rows[1][rows[0].index("Summary")]
    assert summary.startswith("'=")


@pytest.mark.parametrize("dangerous", ["=SUM(A1)", "+1", "-lookup", "@import"])
def test_every_formula_prefix_is_neutralised(client, staff, spa, dangerous):
    ContactLogEntry.objects.create(
        organisation=spa, channel="call", direction="inbound", summary=dangerous,
    )
    client.force_login(staff)
    rows = _rows(_pull(client, "conversations"))
    assert rows[1][rows[0].index("Summary")] == "'" + dangerous


def test_dates_are_iso_so_they_sort_and_parse(client, staff, spa):
    _billable(spa, staff)
    client.force_login(staff)
    rows = _rows(_pull(client, "orders"))
    # Blank is fine; a present one has to be ISO.
    started = rows[1][rows[0].index("Started")]
    assert started == "" or started.count("-") == 2


# ── every report renders ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "report",
    ["invoices", "orders", "quotes", "timesheet", "conversations", "tasks",
     "activity", "clients", "hosting"],
)
def test_every_report_renders_with_a_header(client, staff, spa, report):
    """
    An empty report is a header row, not an empty file. A zero-byte download
    reads as broken, where a header with no rows reads as "nothing in this
    period" — which is the true statement.
    """
    client.force_login(staff)
    rows = _rows(_pull(client, report))
    assert rows, report
    assert len(rows[0]) > 1, report


def test_the_file_is_named_after_the_report_and_the_period(client, staff):
    client.force_login(staff)
    response = _pull(client, "invoices", **{"from": "2026-09-01", "to": "2026-09-30"})
    disposition = response["Content-Disposition"]
    assert "genmars-invoices-2026-09-01-to-2026-09-30.csv" in disposition
    assert response["Content-Type"].startswith("text/csv")


def test_a_snapshot_report_is_named_for_today_not_a_range(client, staff):
    """A date range over "what we run right now" would be meaningless."""
    client.force_login(staff)
    response = _pull(client, "clients", **{"from": "2026-01-01", "to": "2026-09-30"})
    assert "to" not in response["Content-Disposition"].split("filename=")[1]


def test_a_clients_billing_history_is_not_left_in_a_proxy_cache(client, staff):
    client.force_login(staff)
    assert "no-store" in _pull(client, "invoices")["Cache-Control"]


# ── the window ───────────────────────────────────────────────────────────────


def test_the_period_includes_its_last_day(client, staff, spa):
    """
    "To 30 September" means including the 30th, which is not what a naive
    less-than comparison does.
    """
    order = _billable(spa, staff)
    invoice = services.issue_invoice(
        order=order, actor=staff, description="Deposit", amount_kes=Decimal("100.00")
    )
    invoice.issued_on = timezone.localdate()
    invoice.save(update_fields=["issued_on"])

    today = timezone.localdate().isoformat()
    client.force_login(staff)
    rows = _rows(_pull(client, "invoices", **{"from": today, "to": today}))
    assert len(rows) == 2


def test_a_backwards_period_is_refused(client, staff):
    client.force_login(staff)
    response = client.post(
        reverse("ops-reports"),
        {"report": "invoices", "from": "2026-09-30", "to": "2026-09-01"},
        content_type="application/json",
    )
    assert response.status_code == 400
    assert response.json()["field"] == "to"


def test_a_malformed_date_falls_back_rather_than_crashing(client, staff):
    client.force_login(staff)
    response = client.post(
        reverse("ops-reports"),
        {"report": "invoices", "from": "not-a-date"},
        content_type="application/json",
    )
    assert response.status_code == 200


def test_an_unknown_report_is_refused(client, staff):
    client.force_login(staff)
    response = client.post(
        reverse("ops-reports"), {"report": "everything"},
        content_type="application/json",
    )
    assert response.status_code == 400


# ── the record ───────────────────────────────────────────────────────────────


def test_every_export_is_logged(client, staff, spa):
    """
    Not a barrier — every staff account can already read all of it on a screen,
    and a control defeated by copy-paste is theatre. It is recorded because a
    bulk export is exactly the shape of act somebody would want to reconstruct
    afterwards.
    """
    client.force_login(staff)
    _pull(client, "conversations")

    entry = ActivityLog.objects.get(action=ActivityLog.Action.REPORT_EXPORTED)
    assert entry.actor == staff
    assert "Client conversations" in entry.summary
    assert entry.detail["report"] == "conversations"


def test_a_client_account_cannot_export_anything(client, spa):
    outsider = User.objects.create_user(
        email="client@example.com", password=PASSWORD, email_verified_at=timezone.now()
    )
    Membership.objects.create(user=outsider, organisation=spa)
    client.force_login(outsider)

    assert client.get(reverse("ops-reports")).status_code == 403
    assert client.post(
        reverse("ops-reports"), {"report": "invoices"}, content_type="application/json"
    ).status_code == 403


def test_the_timesheet_carries_both_minutes_and_hours(client, staff):
    """
    Minutes add up exactly; hours are what anybody reading the sheet actually
    wants to see.
    """
    now = timezone.now()
    Shift.objects.create(
        person=staff, started_at=now - timedelta(hours=3), ended_at=now,
    )
    client.force_login(staff)
    rows = _rows(_pull(client, "timesheet"))
    header = rows[0]
    assert rows[1][header.index("Minutes")] == "180"
    assert rows[1][header.index("Hours")] == "3.00"
