"""
Operations read paths.

The mirror of `portal/selectors.py`, and deliberately its opposite: that module
answers "what may this CLIENT see" and scopes every query through Membership.
This one answers "what does Genmars see", which is everything.

Both are choke points. Neither may call the other. The value of the client one
is that it cannot be widened by accident; the value of this one is that the
widening is confined to a module whose docstring says so, behind `IsStaff`.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from django.db.models import Count, Prefetch, Q, QuerySet
from django.utils import timezone

from accounts.models import Membership, Organisation, User
from portal.models import (
    AccessRequest,
    ActivityLog,
    Blocker,
    ClientProfile,
    ContactLogEntry,
    Contract,
    Decision,
    HostingArrangement,
    DeliveryGate,
    Enquiry,
    Invoice,
    Milestone,
    Order,
    ProgressNote,
    Service,
    Shift,
)


def enquiries(*, status: str | None = None) -> QuerySet[Enquiry]:
    """
    The triage queue, oldest concern first.

    Ordered by creation ASCENDING, unlike every client-facing list. A queue is
    not a feed: the enquiry that has been waiting longest is the one most at
    risk of being forgotten, and putting the newest on top is how a backlog
    quietly grows a tail nobody reads.
    """
    qs = (
        Enquiry.objects.select_related(
            "organisation", "submitted_by", "converted_to", "decided_by"
        )
        .order_by("created_at")
    )
    if status:
        qs = qs.filter(status=status)
    return qs


def open_enquiries() -> QuerySet[Enquiry]:
    """Everything still awaiting a decision."""
    return enquiries().filter(
        status__in=[Enquiry.Status.NEW, Enquiry.Status.QUALIFYING]
    )


def enquiry(pk: int) -> Enquiry | None:
    return enquiries().filter(pk=pk).first()


def orders() -> QuerySet[Order]:
    """
    Every order, across every organisation.

    Newest first here, because this is a register rather than a queue — the
    thing you are looking for is almost always recent.
    """
    return (
        Order.objects.select_related("organisation", "contact", "service")
        .annotate(note_count=Count("notes", distinct=True))
        .order_by("-created_at")
    )


def order(reference: str) -> Order | None:
    """
    One order with everything the workspace needs, in one round trip.

    Prefetched rather than lazily walked: the detail screen renders notes and
    milestones together, and without this it is one query per note author on a
    page that exists to be read quickly.

    Notes are prefetched UNFILTERED — drafts included. That is the difference
    from the client view, which shows published notes only. Staff need to see
    the draft precisely because it is the last point at which it can be edited.
    """
    return (
        orders()
        .prefetch_related(
            Prefetch(
                "notes",
                queryset=ProgressNote.objects.select_related("author").order_by(
                    "-week_of"
                ),
            ),
            Prefetch(
                "milestones",
                queryset=Milestone.objects.order_by("position", "due_on"),
            ),
            Prefetch(
                "gates",
                queryset=DeliveryGate.objects.select_related("met_by").order_by("position"),
            ),
            Prefetch(
                "contracts",
                queryset=Contract.objects.select_related(
                    "issued_by", "recorded_by"
                ).order_by("-version"),
            ),
            Prefetch(
                "blockers",
                queryset=Blocker.objects.select_related("raised_by").order_by(
                    "cleared_at", "-raised_at"
                ),
            ),
        )
        .filter(reference=reference)
        .first()
    )


def queue_counts() -> dict[str, int]:
    """
    The numbers the dashboard header carries.

    `awaiting_note` is the one that earns its place: active orders with no
    published note in the last seven days. Charter 05 §III promises a written
    update every week, same day, even when progress is thin — so a count that
    is not zero is the promise slipping, visible before the client notices
    rather than after they ask.
    """
    from datetime import timedelta

    from django.utils import timezone

    cutoff = timezone.localdate() - timedelta(days=7)
    active = Order.objects.filter(
        status__in=[Order.Status.SCOPING, Order.Status.ACTIVE, Order.Status.REVIEW]
    )
    return {
        "new_enquiries": Enquiry.objects.filter(status=Enquiry.Status.NEW).count(),
        "qualifying": Enquiry.objects.filter(
            status=Enquiry.Status.QUALIFYING
        ).count(),
        "active_orders": active.count(),
        # Carried in the header on every screen, like awaiting_note: a blocker
        # nobody is looking at is the failure mode, and the queue screen is
        # where people spend their time rather than the delivery board.
        "open_blockers": Blocker.objects.filter(cleared_at__isnull=True).count(),
        # Retrospective records are excluded: their notes were never going to
        # be written, for a promise that was not in force at the time. Without
        # this, backfilling a year of past work lights the counter up for
        # engagements that finished long ago — see Order.recorded_retrospectively.
        "awaiting_note": active.exclude(recorded_retrospectively=True)
        .exclude(notes__published_at__isnull=False, notes__week_of__gte=cutoff)
        .count(),
        # A promise made on a call and not yet kept, past the date it was
        # promised for. Carried here for the same reason as awaiting_note:
        # "I'll send you a quote Thursday" is the commonest thing this company
        # says and the commonest thing it drops, and it drops because it lived
        # only in the memory of whoever said it.
        "follow_ups_due": ContactLogEntry.objects.filter(
            cleared_at__isnull=True,
            follow_up__gt="",
            follow_up_by__lte=timezone.localdate(),
        ).count(),
        # A domain lapses silently and the client cannot tell it apart from us
        # having broken something. Thirty days is enough warning to recover a
        # .co.ke without paying a redemption fee.
        # Somebody is stopped and waiting on a person. Carried here for the
        # same reason as the weekly-note count: a request nobody sees is
        # indistinguishable from not having asked.
        "requests_pending": AccessRequest.objects.filter(
            status=AccessRequest.Status.PENDING
        ).count(),
        "renewals_due": HostingArrangement.objects.filter(
            retired_at__isnull=True,
            renews_on__isnull=False,
            renews_on__lte=timezone.localdate() + timedelta(days=30),
        ).count(),
    }


def delivery_overview() -> list[dict]:
    """
    Every active order with its delivery state, for the engineering board.

    Answers the only two questions worth a dashboard: what is blocked, and what
    is not done. An order at 6/6 with nothing open needs no attention and is
    listed last.

    Counts are annotated in SQL rather than walked in Python: one query for the
    board instead of three per order, which matters the day there are thirty of
    them and not three.
    """
    from django.db.models import Q

    active = (
        Order.objects.filter(
            status__in=[Order.Status.SCOPING, Order.Status.ACTIVE, Order.Status.REVIEW]
        )
        .select_related("organisation", "contact")
        .annotate(
            gates_total=Count("gates", distinct=True),
            gates_met=Count(
                "gates", filter=Q(gates__met_at__isnull=False), distinct=True
            ),
            blockers_open=Count(
                "blockers", filter=Q(blockers__cleared_at__isnull=True), distinct=True
            ),
        )
        # Blocked first, then least complete. The board should open on the thing
        # most at risk, not on whatever was created most recently.
        .order_by("-blockers_open", "gates_met", "target_date")
    )

    return [
        {
            "reference": o.reference,
            "title": o.title,
            "organisation": o.organisation.name,
            "contact": {"full_name": o.contact.full_name, "email": o.contact.email},
            "status": o.status,
            "status_label": o.get_status_display(),
            "target_date": o.target_date,
            "gates_total": o.gates_total,
            "gates_met": o.gates_met,
            "blockers_open": o.blockers_open,
        }
        for o in active
    ]


def delivery_counts() -> dict[str, int]:
    """Header numbers for the engineering board."""
    from django.db.models import Q

    active = Order.objects.filter(
        status__in=[Order.Status.SCOPING, Order.Status.ACTIVE, Order.Status.REVIEW]
    )
    return {
        "active_orders": active.count(),
        "open_blockers": Blocker.objects.filter(cleared_at__isnull=True).count(),
        "blocked_on_client": Blocker.objects.filter(
            cleared_at__isnull=True, waiting_on=Blocker.WaitingOn.CLIENT
        ).count(),
        # Orders where every gate is met. Charter 03 §II — this is the only
        # count that means "done", and it is deliberately strict.
        "fully_met": sum(
            1
            for o in active.annotate(
                total=Count("gates", distinct=True),
                met=Count("gates", filter=Q(gates__met_at__isnull=False), distinct=True),
            )
            if o.total > 0 and o.total == o.met
        ),
    }


def organisations(*, include_archived: bool = False) -> QuerySet[Organisation]:
    """
    Client organisations with their people, for the clients screen.

    Archived ones are excluded by default. They are not deleted and are one
    toggle away — see archive_organisation for why hiding rather than deleting
    is the only honest option for a client we have stopped working with.

    Prefetched: a members list that lazily walks memberships is one query per
    organisation plus one per member, on the page whose entire job is showing
    members.
    """
    qs = Organisation.objects.all()
    if not include_archived:
        qs = qs.filter(archived_at__isnull=True)
    return (
        qs.annotate(order_count=Count("orders", distinct=True))
        .prefetch_related(
            Prefetch(
                "memberships",
                queryset=Membership.objects.select_related(
                    "user", "invited_by"
                ).order_by("created_at"),
            )
        )
        .order_by("name")
    )


def organisation(pk: int) -> Organisation | None:
    """
    One client by id, ARCHIVED OR NOT.

    Deliberately not scoped to the default list. Archiving hides a client from
    the screens people work in; it does not make them unreadable, and a link
    from an old invoice or a log entry to a 404 would look like the record had
    been destroyed — which is the exact impression archiving exists to avoid.
    """
    return organisations(include_archived=True).filter(pk=pk).first()


def services() -> QuerySet[Service]:
    """The catalogue, retired offerings included — they are still referenced."""
    return Service.objects.annotate(order_count=Count("orders", distinct=True)).order_by(
        "-is_active", "name"
    )


def demand() -> list[dict]:
    """
    What is actually selling, per service.

    ══════════════════════════════════════════════════════════════════════════
    THREE NUMBERS, BECAUSE ONE OF THEM ON ITS OWN MISLEADS.

      · enquiries — how often it is ASKED for. High interest and nothing else
        means the pitch works and something after it does not.
      · orders    — how often that turned into agreed work. This is the
        conversion, and it is the number that says the offering is real.
      · invoiced  — what it has actually been billed for, voids excluded.

    Ranking by enquiries alone would promote whatever is easiest to click on
    the website. Ranking by money alone would bury a cheap service that half
    the client base buys. Both are shown, and neither is presented as "the"
    answer.
    ══════════════════════════════════════════════════════════════════════════

    ── ENQUIRIES WITH NO SERVICE ARE COUNTED, NOT DROPPED ──────────────────────

    Someone can describe what they need in their own words instead of picking
    from the catalogue, and that is an ordinary route rather than a gap. Those
    land in an "unattributed" row. Hiding them would make the totals here
    disagree with the queue, and a dashboard that disagrees with the list it
    summarises stops being read.
    """
    from decimal import Decimal

    from django.db.models import Sum

    rows: dict[str | None, dict] = {}

    def bucket(service: Service | None) -> dict:
        key = service.slug if service else None
        if key not in rows:
            rows[key] = {
                "slug": key or "",
                "name": service.name if service else "Described in their own words",
                "is_attributed": service is not None,
                "enquiries": 0,
                "orders": 0,
                "declined": 0,
                "invoiced_kes": Decimal("0.00"),
                "paid_kes": Decimal("0.00"),
                "tiers": {},
            }
        return rows[key]

    enquiry_rows = Enquiry.objects.select_related("service", "converted_to")

    for row in enquiry_rows:
        entry = bucket(row.service)
        entry["enquiries"] += 1

        if row.status == Enquiry.Status.CONVERTED:
            entry["orders"] += 1
        elif row.status == Enquiry.Status.DECLINED:
            entry["declined"] += 1

        # The tier they picked, as they picked it. Blank is its own bucket
        # rather than being folded into the first tier.
        label = (row.tier or "").strip() or "No size chosen"
        entry["tiers"][label] = entry["tiers"].get(label, 0) + 1

        order = row.converted_to
        if order is None:
            continue

        totals = order.invoices.exclude(status=Invoice.Status.VOID).aggregate(
            billed=Sum("amount_kes"),
        )
        entry["invoiced_kes"] += totals["billed"] or Decimal("0.00")

        paid = order.invoices.filter(status=Invoice.Status.PAID).aggregate(
            settled=Sum("amount_kes"),
        )
        entry["paid_kes"] += paid["settled"] or Decimal("0.00")

    out = []
    for entry in rows.values():
        # Strings, not Decimals. DRF's JSON encoder turns a Decimal into a
        # float, and money through a float is money that cannot be reconciled —
        # the same rule every serializer in this codebase already follows. The
        # totals here end up in somebody's spreadsheet.
        entry["invoiced_kes"] = f"{entry['invoiced_kes']:.2f}"
        entry["paid_kes"] = f"{entry['paid_kes']:.2f}"

        entry["tiers"] = [
            {"tier": tier, "count": count}
            for tier, count in sorted(
                entry["tiers"].items(), key=lambda pair: (-pair[1], pair[0])
            )
        ]
        out.append(entry)

    # Most enquired first, then by money, so the busiest row is at the top and
    # ties break on the more consequential number.
    # Sorted on the Decimal before it was formatted, so "9.00" does not sort
    # above "10.00" the way a string comparison would.
    out.sort(key=lambda e: (-e["enquiries"], -Decimal(e["invoiced_kes"]), e["name"]))
    return out



# ─────────────────────────────────────────────────────────────────────────────
# The workroom — shifts, streaks, and the decision register
# ─────────────────────────────────────────────────────────────────────────────


def _day_start(day: date) -> datetime:
    """Midnight in Nairobi, as an aware datetime. USE_TZ is on; naive would drift."""
    return timezone.make_aware(datetime.combine(day, time.min), timezone.get_current_timezone())


def open_shift_for(person: User) -> Shift | None:
    """The shift this person is currently in, if any. At most one — see Shift."""
    return Shift.objects.filter(person=person, ended_at__isnull=True).first()


def who_is_in() -> QuerySet[Shift]:
    """
    Everyone clocked in right now.

    The one question this whole feature has to answer well. In a company where
    two of three people are usually somewhere else, "can I ask Asha this now"
    is asked several times a day and currently answered by sending a message
    and waiting to see.
    """
    return (
        Shift.objects.filter(ended_at__isnull=True)
        .select_related("person")
        .order_by("started_at")
    )


def shifts_between(
    *, start: date, end: date, person: User | None = None
) -> QuerySet[Shift]:
    """
    Shifts that STARTED in [start, end], inclusive, in Nairobi time.

    Filtered on the start for the reason Shift.local_date gives: a shift is one
    day's work, and the day it belongs to is the day it began.
    """
    qs = (
        Shift.objects.filter(
            started_at__gte=_day_start(start),
            started_at__lt=_day_start(end + timedelta(days=1)),
        )
        .select_related("person")
        .order_by("-started_at")
    )
    if person is not None:
        qs = qs.filter(person=person)
    return qs


def worked_dates(person: User, *, since: date | None = None) -> set[date]:
    """Every Nairobi date this person clocked in on."""
    qs = Shift.objects.filter(person=person)
    if since is not None:
        qs = qs.filter(started_at__gte=_day_start(since))
    return {timezone.localtime(s).date() for s in qs.values_list("started_at", flat=True)}


def working_streak(person: User, *, today: date | None = None) -> dict:
    """
    Consecutive working days clocked in.

    ══════════════════════════════════════════════════════════════════════════
    A WEEKEND DOES NOT BREAK A STREAK, AND THAT IS THE WHOLE DESIGN.

    A counter that resets every Saturday measures whether you worked the
    weekend, which is the opposite of what this company should be rewarding —
    and a number that is 5 at best is a number nobody looks at twice. So
    Saturday and Sunday are SKIPPED: they extend a streak if worked and are
    invisible if not.

    Public holidays are not modelled and therefore do break a streak. That is a
    known gap rather than a decision, and the honest fix is a holiday calendar,
    not a fudge factor here.
    ══════════════════════════════════════════════════════════════════════════

    ── TODAY BEING BLANK IS NOT A BREAK ────────────────────────────────────────

    A streak counted strictly to today would read zero every morning before the
    first clock-in, which turns a quiet motivator into a daily accusation. So
    it may end today OR yesterday-as-a-working-day; only a missed working day
    with a later one after it ends it.
    """
    today = today or timezone.localdate()
    days = worked_dates(person)
    if not days:
        return {"current": 0, "longest": 0, "last_worked": None, "worked_today": False}

    def is_weekend(d: date) -> bool:
        return d.weekday() >= 5

    # ── current ──
    cursor = today
    if cursor not in days:
        # Walk back over weekend days and at most one unworked working day
        # (today itself, which may simply not have started yet).
        cursor -= timedelta(days=1)
        while is_weekend(cursor) and cursor not in days:
            cursor -= timedelta(days=1)
    current = 0
    while cursor in days or is_weekend(cursor):
        if cursor in days:
            current += 1
        cursor -= timedelta(days=1)

    # ── longest ever ──
    longest = 0
    run = 0
    ordered = sorted(days)
    previous: date | None = None
    for day in ordered:
        if previous is not None:
            gap = previous + timedelta(days=1)
            missed = False
            while gap < day:
                if not is_weekend(gap):
                    missed = True
                    break
                gap += timedelta(days=1)
            run = 1 if missed else run + 1
        else:
            run = 1
        previous = day
        longest = max(longest, run)

    return {
        "current": current,
        "longest": longest,
        "last_worked": max(days),
        "worked_today": today in days,
    }


def workroom(*, days: int = 14, today: date | None = None) -> list[dict]:
    """
    Per-person totals for the window: hours, days, streak, things done.

    "Things done" is a COUNT of activity entries, and it is presented as a count
    and never as a score. Two entries can be a week of work and forty can be an
    afternoon of tidying; the number says somebody was here and acting, which
    is all a count of a log can honestly say.
    """
    today = today or timezone.localdate()
    start = today - timedelta(days=days - 1)

    acted = dict(
        ActivityLog.objects.filter(created_at__gte=_day_start(start), actor__isnull=False)
        .values_list("actor_id")
        .annotate(n=Count("id"))
    )

    rows = []
    for person in User.objects.filter(is_staff=True, is_active=True).order_by("full_name", "email"):
        shifts = list(shifts_between(start=start, end=today, person=person))
        minutes = sum(s.minutes for s in shifts)
        rows.append(
            {
                "id": person.id,
                "name": person.full_name or person.email,
                "email": person.email,
                "role": person.staff_role,
                "minutes": minutes,
                "days": len({s.local_date for s in shifts}),
                "shifts": len(shifts),
                "acted": acted.get(person.id, 0),
                "streak": working_streak(person, today=today),
                "open_since": next((s.started_at for s in shifts if s.is_open), None),
            }
        )
    return rows


def decisions(*, status: str | None = None, q: str | None = None) -> QuerySet[Decision]:
    """
    The register, newest first.

    Reversed and superseded entries are INCLUDED by default. They are most of
    the value — a register that quietly drops its wrong turns teaches the same
    lesson twice.
    """
    qs = Decision.objects.select_related("decided_by", "supersedes").prefetch_related(
        "superseded_by"
    )
    if status:
        qs = qs.filter(status=status)
    if q:
        qs = qs.filter(
            Q(title__icontains=q)
            | Q(context__icontains=q)
            | Q(decision__icontains=q)
            | Q(reference__icontains=q)
        )
    return qs


def decision(pk: int) -> Decision | None:
    return decisions().filter(pk=pk).first()


# ─────────────────────────────────────────────────────────────────────────────
# The client record
# ─────────────────────────────────────────────────────────────────────────────


def client_profile(organisation: Organisation) -> ClientProfile:
    """
    The profile, creating an empty one on first read.

    get_or_create rather than a nullable relation, so every screen and every
    serializer downstream can assume it exists. The alternative is a null check
    in each of them, and the one that gets forgotten is a 500 on a client page.
    """
    profile, _ = ClientProfile.objects.get_or_create(organisation=organisation)
    return profile


def hosting_for(organisation: Organisation, *, include_retired: bool = False):
    qs = HostingArrangement.objects.filter(organisation=organisation).select_related(
        "system"
    )
    if not include_retired:
        qs = qs.filter(retired_at__isnull=True)
    return qs


def renewals_due(*, within_days: int = 30, today: date | None = None):
    """
    Everything lapsing soon, across every client, soonest first.

    Includes arrangements already PAST their date. An expired domain is not
    less urgent than one expiring on Friday — it is the emergency — and a
    window that only looked forward would drop it off the list on the morning
    it actually mattered.
    """
    today = today or timezone.localdate()
    return (
        HostingArrangement.objects.filter(
            retired_at__isnull=True,
            renews_on__isnull=False,
            renews_on__lte=today + timedelta(days=within_days),
        )
        .select_related("organisation", "system")
        .order_by("renews_on")
    )


def contact_log_for(organisation: Organisation) -> QuerySet[ContactLogEntry]:
    return (
        ContactLogEntry.objects.filter(organisation=organisation)
        .select_related("recorded_by", "order")
        .order_by("-happened_at", "-id")
    )


def follow_ups_owed(*, overdue_only: bool = False, today: date | None = None):
    """
    Promises made and not yet kept, oldest first.

    Ordered ASCENDING like the enquiry queue and for the same reason: the one
    that has been waiting longest is the one most at risk of being forgotten,
    and putting the newest on top grows a tail nobody reads.
    """
    qs = ContactLogEntry.objects.filter(
        cleared_at__isnull=True, follow_up__gt=""
    ).select_related("organisation", "recorded_by")
    if overdue_only:
        qs = qs.filter(follow_up_by__lte=(today or timezone.localdate()))
    return qs.order_by("follow_up_by", "happened_at")


def client_record(organisation: Organisation) -> dict:
    """
    Everything about one client on one screen.

    Assembled here rather than by the view so that the client page and any
    future export answer from the same place — the shape of "what we know
    about this client" should not depend on which endpoint asked.
    """
    return {
        "organisation": organisation,
        "profile": client_profile(organisation),
        "hosting": list(hosting_for(organisation, include_retired=True)),
        "contact_log": list(contact_log_for(organisation)[:50]),
        "orders": list(
            Order.objects.filter(organisation=organisation)
            .select_related("contact")
            .order_by("-created_at")
        ),
    }
