"""
Read paths for client-facing data.

═══════════════════════════════════════════════════════════════════════════════
ISOLATION LIVES HERE, AND ONLY HERE.

Every client-facing view reads through these functions. No view may build its
own Order queryset — one forgotten `.filter()` in one view is a confidentiality
breach under Charter 05 §V, and it fails silently: nothing errors, the wrong
client's data simply appears on the page.

Scoping through a single choke point makes that mistake structurally hard
instead of merely unlikely, and gives the isolation tests one thing to prove.
═══════════════════════════════════════════════════════════════════════════════

`is_staff` grants NOTHING here. Genmars staff use the Django admin; these
selectors answer only "what may this account see", and the answer always comes
through Membership.
"""

from __future__ import annotations

from django.db.models import Prefetch, QuerySet

from accounts.models import User
from portal.models import (
    Blocker,
    Contract,
    HostingArrangement,
    Offer,
    Invoice,
    Order,
    OrderSeen,
    SupportTicket,
    System,
)


def organisation_ids_for(user: User) -> QuerySet:
    """The organisations this user belongs to. The root of every scope."""
    return user.memberships.values_list("organisation_id", flat=True)


def orders_for(user: User) -> QuerySet[Order]:
    """
    Orders this user may see.

    Returns an empty queryset for a user with no membership — the common case
    for a fresh signup, and it must be an ordinary empty state rather than an
    error.
    """
    if not user.is_authenticated:
        return Order.objects.none()
    return (
        Order.objects.filter(organisation_id__in=organisation_ids_for(user))
        .select_related("organisation", "contact")
        # Prefetched and scoped to THIS user, so the "have you seen it" check
        # is one query for the whole list rather than one per order — and so
        # it can never accidentally read somebody else's seen record.
        .prefetch_related(
            Prefetch(
                "seen_by",
                queryset=OrderSeen.objects.filter(user=user),
                to_attr=None,
            )
        )
        .order_by("-created_at")
    )


def unseen_notice(user: User, order: Order) -> str | None:
    """
    What changed on this order since this person last looked, if anything.

    Returns the reason in the client's own words — "Scope changed", "New
    progress note" — or None. The words matter: a bare dot tells somebody
    there is something to find without telling them whether it is worth
    finding, and half of them will not go looking.

    ── AN ORDER THEY HAVE NEVER OPENED COUNTS AS UNSEEN ────────────────────────

    Deliberately. A client who has never looked at an order has not seen the
    scope on it, and that is exactly the state the marker exists for.
    """
    if order.client_notice_at is None:
        return None
    seen = next(iter(order.seen_by.all()), None)
    if seen is not None and seen.seen_at >= order.client_notice_at:
        return None
    return order.client_notice_reason or "Updated"


def mark_order_seen(user: User, order: Order) -> None:
    """
    Stamp that this person has now looked at it.

    Only ever called from the client's own order page — the one place where
    somebody has demonstrably read what is on it. Marking it from a list view
    would clear the marker for an order they scrolled past.
    """
    from portal.models import OrderSeen

    OrderSeen.objects.update_or_create(user=user, order=order)


def order_for(user: User, reference: str) -> Order | None:
    """
    One order by reference, scoped to the user.

    Returns None for an order that exists but belongs to someone else — the same
    answer as for one that does not exist at all. A 403 here would confirm the
    reference is real, which is an enumeration oracle over client names.
    """
    return orders_for(user).filter(reference=reference).first()


def invoice_for(user: User, reference: str, number: str) -> Invoice | None:
    """
    One invoice, scoped to the user through its order.

    Goes through `order_for` rather than querying Invoice directly. That is the
    whole discipline of this module: an invoice number is guessable
    (GM-INV-2026-0004), and a query that started from Invoice would hand one
    client another client's billing document the first time somebody forgot a
    filter. Starting from the order means the isolation is inherited and cannot
    be forgotten here.
    """
    order = order_for(user, reference)
    if order is None:
        return None
    return order.invoices.filter(number=number).first()


def invoices_for(user: User) -> QuerySet[Invoice]:
    """
    Every invoice addressed to the user's organisations.

    ── WHY THIS DOES NOT GO THROUGH orders_for ─────────────────────────────────

    `invoice_for` below scopes through the order, and that was right when every
    invoice had one. Direct invoices do not — a renewal billed to a past client
    has no project — so an order-scoped query cannot see them at all, and the
    client would be asked to pay a document their portal says does not exist.

    So this scopes on `organisation` instead. That is not a weaker filter: an
    invoice's organisation is written once, from the order, when it is issued,
    and `test_an_invoice_never_disagrees_with_its_order_about_the_client`
    proves the two cannot disagree. The isolation is the same isolation; it is
    just read from the invoice rather than inferred through a join.
    """
    return (
        Invoice.objects.filter(organisation_id__in=organisation_ids_for(user))
        .select_related("order", "organisation")
        .prefetch_related("payments")
    )


def client_invoice_for(user: User, number: str) -> Invoice | None:
    """
    One invoice by number, scoped to the user.

    An invoice number is guessable (GM-INV-2026-0004), so this MUST start from
    the user's organisations rather than from Invoice. It does — see
    `invoices_for` — and there is a test that hands one client another's
    number and expects a 404.
    """
    return invoices_for(user).filter(number=number).first()


def offers_for(user: User) -> QuerySet[Offer]:
    """
    Offers this client has actually been sent.

    ── DRAFTS ARE EXCLUDED HERE, NOT IN THE VIEW ───────────────────────────────

    An offer we have not sent is not one they have received, and showing it
    would put a price in front of somebody before we had decided to. That rule
    used to live inline in the view; it belongs in this module with every other
    client scope, so a second view — the document route — cannot get it subtly
    different.
    """
    if not user.is_authenticated:
        return Offer.objects.none()
    return (
        Offer.objects.filter(organisation_id__in=organisation_ids_for(user))
        .exclude(status=Offer.Status.DRAFT)
        .select_related("organisation", "service")
        .order_by("-created_at")
    )


def offer_for(user: User, reference: str) -> Offer | None:
    """
    One offer by reference, scoped to the client.

    Returns None for an offer belonging to someone else — the same answer as
    for one that does not exist. An offer reference is sequential and therefore
    guessable, so a 403 would confirm which are real and turn this into a
    counter of how much business Genmars is quoting, and to whom.
    """
    return offers_for(user).filter(reference=reference).first()


def published_notes_for(order: Order) -> QuerySet:
    """
    Published notes only. A draft is not a promise; Charter 05 §I is about what
    the client was actually told.
    """
    return (
        order.notes.filter(published_at__isnull=False)
        .select_related("author")
        .order_by("-week_of")
    )


def export_payload(user: User) -> dict:
    """
    Everything we hold about this user, as plain JSON.

    Charter 05 §VIII: "We do not hold data, domains, or accounts hostage under
    any circumstance." Shipping this in v1 rather than later is the point — an
    export endpoint is trivial now and awkward to retrofit, and a portal that
    cannot hand your data back is exactly the hostage-taking the charter rules
    out.
    """
    return {
        "account": {
            "email": user.email,
            "full_name": user.full_name,
            "joined": user.date_joined.isoformat(),
            "email_verified": user.is_email_verified,
        },
        "organisations": [
            {"name": m.organisation.name, "role": m.role}
            for m in user.memberships.select_related("organisation")
        ],
        "orders": [
            {
                "reference": o.reference,
                "title": o.title,
                "scope": o.scope,
                "exclusions": o.exclusions,
                "status": o.get_status_display(),
                "target_date": o.target_date.isoformat() if o.target_date else None,
                # Charter 05 §VIII — we do not hold anything hostage, and the
                # agreement itself is the single most important thing a client
                # could want a copy of.
                "contract": (
                    {
                        "reference": c.reference,
                        "version": c.version,
                        "scope": c.scope,
                        "exclusions": c.exclusions,
                        "deliverables": c.deliverable_list,
                        "total_kes": str(c.total_kes),
                        "payment_terms": c.payment_terms,
                        "issued": c.issued_at.isoformat() if c.issued_at else None,
                        "signed_on": c.signed_on.isoformat() if c.signed_on else None,
                        "signed_by": c.signed_by_name,
                    }
                    if (c := live_contract_for(o))
                    else None
                ),
                "progress_notes": [
                    {
                        "week_of": n.week_of.isoformat(),
                        "body": n.body,
                        "published": n.published_at.isoformat() if n.published_at else None,
                    }
                    for n in published_notes_for(o)
                ],
                "milestones": [
                    {
                        "name": m.name,
                        "amount_kes": str(m.amount_kes),
                        "due_on": m.due_on.isoformat() if m.due_on else None,
                        "status": m.get_status_display(),
                    }
                    for m in o.milestones.all()
                ],
                # Charter 05 §VIII again. What they were billed and what they
                # paid is among the first things anyone wants a copy of, and a
                # client who leaves should not have to ask us for their own
                # billing history.
                "invoices": [
                    {
                        "number": i.number,
                        "description": i.description,
                        "amount_kes": str(i.amount_kes),
                        "status": i.get_status_display(),
                        "issued_on": i.issued_on.isoformat(),
                        "due_on": i.due_on.isoformat() if i.due_on else None,
                        "paid_on": i.paid_on.isoformat() if i.paid_on else None,
                        "payment_reference": i.payment_reference,
                        "void_reason": i.void_reason,
                    }
                    for i in o.invoices.all()
                ],
            }
            for o in orders_for(user)
        ],
    }


def live_contract_for(order: Order) -> Contract | None:
    """
    The statement of work currently in force, for the client's own order.

    Issued or signed only. A DRAFT is not something the client has been shown,
    and a SUPERSEDED or VOID one is not what is in force — surfacing either
    would tell a client a different deal applies than the one that does.

    The newest live version wins. There should only ever be one, because
    issuing supersedes the previous, but ordering by version rather than
    trusting that is cheaper than the bug if it is ever untrue.
    """
    return (
        order.contracts.filter(
            status__in=[Contract.Status.ISSUED, Contract.Status.SIGNED]
        )
        .order_by("-version")
        .first()
    )


def tickets_for(user: User) -> QuerySet[SupportTicket]:
    """
    Support requests belonging to the user's organisations.

    Scoped on organisation in the query itself, like every other client read in
    this module. A ticket reference is guessable (GM-SUP-2026-0004), so a
    lookup that started from SupportTicket would hand one client another's
    conversation the first time somebody forgot a filter.
    """
    return (
        SupportTicket.objects.filter(organisation_id__in=organisation_ids_for(user))
        .select_related("order", "organisation")
        .prefetch_related("messages")
    )


def ticket_for(user: User, reference: str) -> SupportTicket | None:
    return tickets_for(user).filter(reference=reference).first()


def waiting_on_client(user: User) -> QuerySet[Blocker]:
    """
    Open blockers where WE are waiting on THEM.

    ══════════════════════════════════════════════════════════════════════════
    THE MOST USEFUL THING THIS PORTAL CAN TELL A CLIENT.

    The commonest way a project stalls is not that anybody stopped working. It
    is that we are waiting on something — an export, an approval, a login — and
    the client does not know it, because the only place that fact lived was a
    blocker on an internal delivery board.

    Two weeks later both sides believe the other is being slow. Nobody was
    doing anything wrong; the information was simply on one side of a wall.
    ══════════════════════════════════════════════════════════════════════════

    Deliberately ONLY `waiting_on = client`. Blockers on us are ours to fix and
    listing them here would read as excuses; blockers on a third party are
    usually a supplier the client has no lever over. Neither is something they
    can act on, and a list of things you cannot act on is noise.
    """
    return (
        Blocker.objects.filter(
            order__organisation_id__in=organisation_ids_for(user),
            waiting_on=Blocker.WaitingOn.CLIENT,
            cleared_at__isnull=True,
        )
        .select_related("order")
        .order_by("raised_at")
    )


def ongoing_work_for(user: User) -> QuerySet[Order]:
    """
    Work that is still running: retainers, upkeep, and open projects.

    ══════════════════════════════════════════════════════════════════════════
    A CLIENT SHOULD NOT HAVE TO ASK WHAT THEY ARE STILL PAYING FOR.

    Once past work started being recorded here, a client's order list became a
    mixture of things finished years ago and things still live, in one
    undifferentiated column. The commonest question a client has about a
    retainer — "is this still running, and what does it cover" — was answerable
    only by reading every row and inferring.

    An arrangement somebody is paying for and cannot easily see is the shape of
    a bad supplier relationship, whether or not anybody intended it.
    ══════════════════════════════════════════════════════════════════════════
    """
    return orders_for(user).filter(
        status__in=[Order.Status.SCOPING, Order.Status.ACTIVE, Order.Status.REVIEW]
    )


def hosting_for_client(user: User) -> QuerySet[HostingArrangement]:
    """
    Domains, hosting and mailboxes Genmars runs or renews for this client.

    ══════════════════════════════════════════════════════════════════════════
    CHARTER 05 §VIII — "WE DO NOT HOLD DATA, DOMAINS, OR ACCOUNTS HOSTAGE."

    That promise is worth very little if the client cannot see which of their
    accounts we hold. A domain registered in Genmars' name is one they cannot
    take with them without asking us, and until now the only place that fact
    existed was an operations screen they have no access to.

    Showing it is the promise being kept rather than asserted. It is also the
    thing most likely to be discovered at the worst moment — when they want to
    leave, or when we are unreachable and something has expired.
    ══════════════════════════════════════════════════════════════════════════

    Retired arrangements are excluded: this answers "what is running now". The
    record of one we used to hold stays in operations, where the question is
    historical rather than practical.
    """
    return (
        HostingArrangement.objects.filter(
            organisation_id__in=organisation_ids_for(user),
            retired_at__isnull=True,
        )
        .select_related("system")
        .order_by("renews_on", "identifier")
    )


def systems_for(user: User) -> QuerySet[System]:
    """
    Systems Genmars runs FOR this client, as the client may see them.

    Scoped on `organisation`, which is set only where we run something on a
    client's behalf. Our own internal systems have no organisation and are
    therefore invisible here — that is the filter doing its job, not an
    omission.

    What reaches them is decided by ClientSystemSerializer, not by this
    queryset: the runbook, the health-check URL and the reporting keys are
    ours, and none of them are a fact about their service being up.
    """
    return System.objects.filter(
        organisation_id__in=organisation_ids_for(user)
    ).exclude(status=System.Status.RETIRED)

