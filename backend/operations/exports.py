"""
Reports out of the system, as CSV.

═══════════════════════════════════════════════════════════════════════════════
CSV, AND NOTHING ELSE.

Not XLSX, not PDF. Both need a library and Charter 03 §I says a thing enters
the stack only when what is already there cannot do the job — and `csv` is in
the standard library. More to the point, every one of these is going to be
opened in a spreadsheet, filtered, and pasted into something: XLSX would add a
dependency to produce a file people immediately convert back.

═══════════════════════════════════════════════════════════════════════════════

── WHAT THESE ARE FOR ──────────────────────────────────────────────────────────

An accountant at year end, a bank asking what the company bills, a founder
working out where the month went. All of them want a spreadsheet, none of them
want to read a screen.

── AMOUNTS ARE PLAIN, UNFORMATTED DECIMALS ─────────────────────────────────────

"KES 45,000.00" is a string that arrives in a spreadsheet as text and cannot be
summed. The column header says what the currency is; the cell holds a number.
This is the single most common way an exported report becomes useless.

── AND NOTHING HERE INVENTS A ROW ──────────────────────────────────────────────

Every export is a straight read of what is recorded. A report that helpfully
fills a blank — a missing date as today, an unset amount as zero — produces a
spreadsheet somebody then reconciles against reality and cannot.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, timedelta
from io import StringIO

from django.utils import timezone

from accounts.models import Organisation, User
from portal.models import (
    ActivityLog,
    ContactLogEntry,
    HostingArrangement,
    Invoice,
    Offer,
    Order,
    Shift,
    Task,
)


@dataclass
class Report:
    key: str
    label: str
    # What the reader gets, in a sentence. Shown beside the download, because
    # picking the wrong report and finding out in a spreadsheet is a slow way
    # to learn what these contain.
    describes: str
    # Whether a date range narrows it. Some reports are a snapshot of now
    # (what we run, who our clients are) and a range would be meaningless.
    dated: bool = True


REPORTS: dict[str, Report] = {
    r.key: r
    for r in [
        Report(
            "invoices",
            "Invoices",
            "Every invoice issued in the period, with what has been paid against it.",
        ),
        Report(
            "orders",
            "Work",
            "Orders opened in the period, their scope, status and dates.",
        ),
        Report(
            "quotes",
            "Quotes",
            "Offers sent in the period, what was quoted and what happened to them.",
        ),
        Report(
            "timesheet",
            "Hours",
            "Shifts clocked in the period, one row each, with who and how long.",
        ),
        Report(
            "conversations",
            "Client conversations",
            "The contact log for the period. Internal — do not send this to a client.",
        ),
        Report(
            "tasks",
            "Tasks",
            "Work on the board, open and finished, with what each is about.",
        ),
        Report(
            "activity",
            "Audit log",
            "Every consequential act in the period, in order, with who did it.",
        ),
        Report(
            "clients",
            "Clients",
            "Every client, their contact details and what we run for them.",
            dated=False,
        ),
        Report(
            "hosting",
            "Domains and hosting",
            "What we run or renew, whose name each account is in, and when it lapses.",
            dated=False,
        ),
    ]
}


def _decimal(value) -> str:
    """
    A number a spreadsheet can add up, or an empty cell.

    Never "0" for something unset. Zero is a fact — an invoice for nothing —
    and a blank is the absence of one; a report that writes zero for both makes
    the difference unrecoverable at exactly the point somebody is totalling a
    column.
    """
    return "" if value is None else str(value)


def _day(value: date | None) -> str:
    """ISO, always. It sorts as text and every spreadsheet parses it."""
    return value.isoformat() if value else ""


def _rows(key: str, start: date, end: date):
    """Header row first, then the data. Yields lists of strings."""
    # A whole-day window: `end` is inclusive, which is what a person choosing
    # "to 30 September" means and not what a naive `<` comparison does.
    tz = timezone.get_current_timezone()
    from datetime import datetime, time

    begins = timezone.make_aware(datetime.combine(start, time.min), tz)
    finishes = timezone.make_aware(datetime.combine(end + timedelta(days=1), time.min), tz)

    if key == "invoices":
        yield [
            "Number", "Client", "Description", "Amount KES", "Paid KES",
            "Status", "Issued", "Due", "Paid on", "Order",
        ]
        for invoice in (
            Invoice.objects.filter(issued_on__gte=start, issued_on__lte=end)
            .select_related("organisation", "order")
            .prefetch_related("payments")
            .order_by("issued_on", "number")
        ):
            payments = list(invoice.payments.all())
            # None rather than zero when nothing has been paid — see _decimal.
            # "0.00" and "nothing recorded" are different facts and a totalled
            # column cannot tell them apart afterwards.
            paid = sum(p.amount_kes for p in payments) if payments else None
            yield [
                invoice.number,
                invoice.billed_to_name or invoice.organisation.name,
                invoice.description,
                _decimal(invoice.amount_kes),
                _decimal(paid),
                invoice.get_status_display(),
                _day(invoice.issued_on),
                _day(invoice.due_on),
                _day(invoice.paid_on),
                invoice.order.reference if invoice.order_id else "",
            ]

    elif key == "orders":
        yield [
            "Reference", "Client", "Title", "Kind", "Status",
            "Started", "Target", "Finished", "Recorded after the fact", "Scope",
        ]
        for order in (
            Order.objects.filter(created_at__gte=begins, created_at__lt=finishes)
            .select_related("organisation")
            .order_by("created_at")
        ):
            yield [
                order.reference,
                order.organisation.name,
                order.title,
                order.get_kind_display(),
                order.get_status_display(),
                _day(order.started_on),
                _day(order.target_date),
                _day(order.completed_on),
                "yes" if order.recorded_retrospectively else "no",
                order.scope,
            ]

    elif key == "quotes":
        yield [
            "Reference", "Client", "Title", "Amount KES", "List price KES",
            "Status", "Sent", "Expires", "Decided",
        ]
        for offer in (
            Offer.objects.filter(created_at__gte=begins, created_at__lt=finishes)
            .select_related("organisation")
            .order_by("created_at")
        ):
            yield [
                offer.reference,
                offer.offered_to_name or offer.organisation.name,
                offer.title,
                _decimal(offer.amount_kes),
                _decimal(offer.list_price_kes),
                offer.get_status_display(),
                _day(offer.sent_at.date() if offer.sent_at else None),
                _day(offer.expires_on),
                _day(offer.decided_at.date() if offer.decided_at else None),
            ]

    elif key == "timesheet":
        yield ["Date", "Who", "In", "Out", "Minutes", "Hours", "Entered afterwards", "On"]
        for shift in (
            Shift.objects.filter(started_at__gte=begins, started_at__lt=finishes)
            .select_related("person")
            .order_by("started_at")
        ):
            local_in = timezone.localtime(shift.started_at)
            local_out = timezone.localtime(shift.ended_at) if shift.ended_at else None
            yield [
                _day(local_in.date()),
                shift.person.full_name or shift.person.email,
                local_in.strftime("%H:%M"),
                local_out.strftime("%H:%M") if local_out else "",
                str(shift.minutes),
                # Both, because minutes add up exactly and hours are what
                # anybody reading the sheet actually wants to see.
                f"{shift.minutes / 60:.2f}",
                "yes" if shift.ended_late else "no",
                shift.started_note or shift.ended_note,
            ]

    elif key == "conversations":
        yield [
            "When", "Client", "Channel", "Direction", "With", "Summary",
            "Follow-up", "Follow-up by", "Cleared", "Recorded by",
        ]
        for entry in (
            ContactLogEntry.objects.filter(
                happened_at__gte=begins, happened_at__lt=finishes
            )
            .select_related("organisation")
            .order_by("happened_at")
        ):
            yield [
                timezone.localtime(entry.happened_at).strftime("%Y-%m-%d %H:%M"),
                entry.organisation.name,
                entry.get_channel_display(),
                entry.get_direction_display(),
                entry.with_whom,
                entry.summary,
                entry.follow_up,
                _day(entry.follow_up_by),
                "yes" if entry.cleared_at else "no",
                entry.recorded_by_label,
            ]

    elif key == "tasks":
        yield [
            "Title", "Assigned to", "Status", "Priority", "Due", "Done",
            "Client", "Order", "From a conversation",
        ]
        for task in (
            Task.objects.filter(created_at__gte=begins, created_at__lt=finishes)
            .select_related("assignee", "organisation", "order", "contact")
            .order_by("created_at")
        ):
            yield [
                task.title,
                task.assignee.full_name or task.assignee.email,
                task.get_status_display(),
                task.get_priority_display(),
                _day(task.due_on),
                _day(task.done_at.date() if task.done_at else None),
                task.organisation.name if task.organisation_id else "",
                task.order.reference if task.order_id else "",
                task.contact.summary if task.contact_id else "",
            ]

    elif key == "activity":
        yield ["When", "Who", "Action", "Subject", "Client", "Summary"]
        for entry in (
            ActivityLog.objects.filter(created_at__gte=begins, created_at__lt=finishes)
            .select_related("organisation")
            .order_by("created_at")
        ):
            yield [
                timezone.localtime(entry.created_at).strftime("%Y-%m-%d %H:%M"),
                entry.actor_label,
                entry.get_action_display(),
                entry.subject,
                entry.organisation.name if entry.organisation_id else "",
                entry.summary,
            ]

    elif key == "clients":
        yield [
            "Client", "What they do", "Contact", "Phone", "Email",
            "Reach them by", "Client since", "Orders", "May be named publicly",
            "Archived",
        ]
        for org in (
            Organisation.objects.select_related("profile")
            .prefetch_related("orders")
            .order_by("name")
        ):
            profile = getattr(org, "profile", None)
            yield [
                org.name,
                profile.what_they_do if profile else "",
                profile.contact_name if profile else "",
                profile.contact_phone if profile else "",
                profile.contact_email if profile else "",
                profile.get_preferred_channel_display() if profile and profile.preferred_channel else "",
                _day(profile.client_since) if profile else "",
                str(org.orders.count()),
                "yes" if profile and profile.may_be_named else "no",
                "yes" if org.archived_at else "no",
            ]

    elif key == "hosting":
        yield [
            "What", "Kind", "Client", "Provider", "Account in whose name",
            "Renews", "Auto-renews", "Costs us KES", "We charge KES", "Retired",
        ]
        for arrangement in (
            HostingArrangement.objects.select_related("organisation")
            .order_by("renews_on", "identifier")
        ):
            yield [
                arrangement.identifier,
                arrangement.get_kind_display(),
                arrangement.organisation.name,
                arrangement.provider,
                arrangement.get_account_holder_display(),
                _day(arrangement.renews_on),
                "yes" if arrangement.auto_renew else "no",
                _decimal(arrangement.annual_cost_kes),
                _decimal(arrangement.annual_charge_kes),
                "yes" if arrangement.retired_at else "no",
            ]


def render(key: str, start: date, end: date) -> str:
    """
    The CSV, as text.

    ── EXCEL AND THE LEADING-EQUALS PROBLEM ────────────────────────────────────

    A cell beginning = + - or @ is treated as a FORMULA by Excel, Numbers and
    Sheets. Client-supplied text lands in these files — a conversation summary,
    an order scope — so a summary that happens to start with "-" becomes a
    broken formula at best, and at worst a spreadsheet that runs something.
    Prefixing a quote neutralises it and the cell still reads correctly.
    """
    out = StringIO()
    # QUOTE_MINIMAL with the default dialect: correct for Excel, and the
    # newline="" contract is met by StringIO.
    writer = csv.writer(out)
    for row in _rows(key, start, end):
        writer.writerow([_safe(cell) for cell in row])
    return out.getvalue()


def _safe(cell: str) -> str:
    text = "" if cell is None else str(cell)
    if text and text[0] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + text
    return text


def filename(key: str, start: date, end: date) -> str:
    report = REPORTS[key]
    if not report.dated:
        return f"genmars-{key}-{timezone.localdate():%Y-%m-%d}.csv"
    return f"genmars-{key}-{start:%Y-%m-%d}-to-{end:%Y-%m-%d}.csv"
