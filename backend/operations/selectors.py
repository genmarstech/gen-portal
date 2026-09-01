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

from portal.models import Enquiry, Milestone, Order, ProgressNote


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
        Order.objects.select_related("organisation", "contact")
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
        "awaiting_note": active.exclude(
            notes__published_at__isnull=False, notes__week_of__gte=cutoff
        ).count(),
    }
