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

from django.db.models import QuerySet

from accounts.models import User
from portal.models import Contract, Invoice, Order


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
        .order_by("-created_at")
    )


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
