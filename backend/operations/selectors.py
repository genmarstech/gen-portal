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

from django.db.models import Count, Prefetch, QuerySet

from accounts.models import Membership, Organisation
from portal.models import (
    Blocker,
    Contract,
    DeliveryGate,
    Enquiry,
    Invoice,
    Milestone,
    Order,
    ProgressNote,
    Service,
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
        "awaiting_note": active.exclude(
            notes__published_at__isnull=False, notes__week_of__gte=cutoff
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


def organisations() -> QuerySet[Organisation]:
    """
    Every client organisation with its people, for the accounts screen.

    Prefetched: a members list that lazily walks memberships is one query per
    organisation plus one per member, on the page whose entire job is showing
    members.
    """
    return (
        Organisation.objects.annotate(order_count=Count("orders", distinct=True))
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
    return organisations().filter(pk=pk).first()


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

