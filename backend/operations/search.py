"""
One search box over everything the company holds.

═══════════════════════════════════════════════════════════════════════════════
WHAT MAKES A SEARCH USEFUL HERE IS RANKING, NOT REACH.

Searching more tables is easy. The hard part is that somebody typing
"GM-INV-2026-0004" wants exactly one row, and somebody typing "kilimani" wants
a client — and a query that returns forty rows ordered by primary key serves
neither. So:

  · A REFERENCE MATCH WINS. References are what people actually paste, out of
    an email or a bank statement, and a reference is unambiguous by
    construction. Those float to the top regardless of type.

  · EACH TYPE IS CAPPED. Without a cap, one client with eighty conversations
    buries every order, invoice and decision under a wall of chat summaries,
    and the box looks broken rather than thorough.

  · TWO CHARACTERS MINIMUM. "a" is a full-table scan across a dozen models to
    produce something nobody can read.

═══════════════════════════════════════════════════════════════════════════════

STAFF ONLY. Every row this touches is internal — the contact log especially,
which is written honestly because nobody outside Genmars reads it. There is no
client-facing counterpart to this module and there must not be one; the client
side has its own scoped selectors in portal/selectors.py.

── WHAT IS DELIBERATELY NOT SEARCHABLE ──────────────────────────────────────

Amounts. A search for "5000" that returns invoices looks helpful and is not:
it matches 5000, 15000 and 50000 with equal confidence, and money is the one
thing where a nearly-right answer is worse than none. Invoices are found by
number, by client, or on the billing screen where the figures are columns you
can compare.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.db.models import Q

from accounts.models import Organisation, User
from portal.models import (
    ChangeRequest,
    ContactLogEntry,
    Contract,
    Decision,
    HostingArrangement,
    Invoice,
    Offer,
    Order,
    SupportTicket,
    System,
    Task,
)

MIN_QUERY = 2
PER_TYPE = 5

# ── the filters, in the order they appear ────────────────────────────────────
#
# A FIXED ORDER, not one derived from how many of each turned up. A filter row
# that reorders itself between searches is one you have to read every time
# rather than aim at — and aiming is the whole point of a filter you use twice
# a day.
#
# Roughly: who, then what was agreed, then what was said, then what we run.
KINDS = (
    "Client",
    "Order",
    "Invoice",
    "Quote",
    "Contract",
    "Support",
    "Change",
    "Conversation",
    "Task",
    "Decision",
    "System",
    "Hosting",
    "Colleague",
)


@dataclass
class Hit:
    """One result, in the shape the palette renders."""

    kind: str
    label: str
    sublabel: str
    href: str
    # True when the query matched a reference rather than prose. These sort
    # first: a reference is what somebody pastes out of an email, and it is
    # unambiguous by construction.
    exact: bool = False

    def as_dict(self) -> dict:
        return {
            "kind": self.kind,
            "label": self.label,
            "sublabel": self.sublabel,
            "href": self.href,
            "exact": self.exact,
        }


def _matches_reference(value: str, q: str) -> bool:
    return bool(value) and q.lower() in value.lower()


def search(q: str, kind: str | None = None) -> list[dict]:
    """
    Everything matching, best first.

    Returns a flat list rather than groups on purpose: the screen groups by
    `kind` for display, but ranking has to be able to put an invoice above a
    client when the invoice number was what was typed. Grouping in the backend
    would make that impossible without a second ordering nobody can see.
    """
    q = (q or "").strip()
    if len(q) < MIN_QUERY:
        return []

    hits: list[Hit] = []

    # ── clients ─────────────────────────────────────────────────────────────
    for org in Organisation.objects.filter(name__icontains=q)[:PER_TYPE]:
        hits.append(
            Hit(
                kind="Client",
                label=org.name,
                sublabel="Archived" if org.archived_at else "",
                href=f"/clients/{org.id}",
            )
        )

    # ── orders ──────────────────────────────────────────────────────────────
    for order in (
        Order.objects.filter(
            Q(reference__icontains=q) | Q(title__icontains=q) | Q(scope__icontains=q)
        )
        .select_related("organisation")[:PER_TYPE]
    ):
        hits.append(
            Hit(
                kind="Order",
                label=f"{order.reference} — {order.title}",
                sublabel=f"{order.organisation.name} · {order.get_status_display()}",
                href=f"/orders/{order.reference}",
                exact=_matches_reference(order.reference, q),
            )
        )

    # ── invoices ────────────────────────────────────────────────────────────
    #
    # By number and description only. Never by amount — see this module's
    # docstring.
    for invoice in (
        Invoice.objects.filter(
            Q(number__icontains=q) | Q(description__icontains=q)
        )
        .select_related("organisation")[:PER_TYPE]
    ):
        hits.append(
            Hit(
                kind="Invoice",
                label=f"{invoice.number} — {invoice.description}",
                sublabel=f"{invoice.organisation.name} · {invoice.get_status_display()}",
                href="/billing",
                exact=_matches_reference(invoice.number, q),
            )
        )

    # ── offers ──────────────────────────────────────────────────────────────
    for offer in (
        Offer.objects.filter(
            Q(reference__icontains=q) | Q(title__icontains=q) | Q(detail__icontains=q)
        )
        .select_related("organisation")[:PER_TYPE]
    ):
        hits.append(
            Hit(
                kind="Quote",
                label=f"{offer.reference} — {offer.title}",
                sublabel=f"{offer.organisation.name} · {offer.get_status_display()}",
                href="/offers",
                exact=_matches_reference(offer.reference, q),
            )
        )

    # ── support ─────────────────────────────────────────────────────────────
    for ticket in (
        SupportTicket.objects.filter(
            Q(reference__icontains=q) | Q(subject__icontains=q)
        )
        .select_related("organisation")[:PER_TYPE]
    ):
        hits.append(
            Hit(
                kind="Support",
                label=f"{ticket.reference} — {ticket.subject}",
                sublabel=f"{ticket.organisation.name} · {ticket.get_status_display()}",
                href="/support",
                exact=_matches_reference(ticket.reference, q),
            )
        )

    # ── conversations ───────────────────────────────────────────────────────
    #
    # Searched across summary AND detail: the thing somebody half-remembers is
    # usually a phrase from the middle of a call, not the one-line summary.
    for entry in (
        ContactLogEntry.objects.filter(
            Q(summary__icontains=q)
            | Q(detail__icontains=q)
            | Q(with_whom__icontains=q)
            | Q(follow_up__icontains=q)
        )
        .select_related("organisation")[:PER_TYPE]
    ):
        hits.append(
            Hit(
                kind="Conversation",
                label=entry.summary,
                sublabel=(
                    f"{entry.organisation.name} · "
                    f"{entry.get_channel_display()}, {entry.happened_at:%-d %b %Y}"
                ),
                href=f"/clients/{entry.organisation_id}",
            )
        )

    # ── decisions ───────────────────────────────────────────────────────────
    for decision in Decision.objects.filter(
        Q(reference__icontains=q)
        | Q(title__icontains=q)
        | Q(context__icontains=q)
        | Q(decision__icontains=q)
    )[:PER_TYPE]:
        hits.append(
            Hit(
                kind="Decision",
                label=f"{decision.reference} — {decision.title}",
                sublabel=decision.get_status_display(),
                href="/decisions",
                exact=_matches_reference(decision.reference, q),
            )
        )

    # ── change requests ─────────────────────────────────────────────────────
    #
    # Searchable by the client's own words, because that is how anybody looks
    # for one: nobody remembers GM-CR-2026-0003, they remember "the Google Ads
    # thing". The classification rides in the sublabel so a search result
    # answers "were we paid for that?" without a click.
    for change in ChangeRequest.objects.filter(
        Q(reference__icontains=q) | Q(summary__icontains=q) | Q(detail__icontains=q)
    ).select_related("organisation", "order")[:PER_TYPE]:
        hits.append(
            Hit(
                kind="Change",
                label=f"{change.reference} — {change.summary}",
                sublabel=" · ".join(
                    part
                    for part in (
                        change.organisation.name,
                        change.get_classification_display()
                        if change.classification
                        else "not yet classified",
                    )
                    if part
                ),
                href="/changes",
                exact=_matches_reference(change.reference, q),
            )
        )

    # ── tasks ───────────────────────────────────────────────────────────────
    for task in (
        Task.objects.filter(Q(title__icontains=q) | Q(detail__icontains=q))
        .exclude(status=Task.Status.DONE)
        .select_related("assignee", "organisation")[:PER_TYPE]
    ):
        hits.append(
            Hit(
                kind="Task",
                label=task.title,
                sublabel=" · ".join(
                    part
                    for part in (
                        task.organisation.name if task.organisation_id else "",
                        task.assignee.full_name or task.assignee.email,
                    )
                    if part
                ),
                href="/team",
            )
        )

    # ── contracts ───────────────────────────────────────────────────────────
    for contract in (
        Contract.objects.filter(Q(title__icontains=q) | Q(scope__icontains=q))
        .select_related("order", "order__organisation")[:PER_TYPE]
    ):
        hits.append(
            Hit(
                kind="Contract",
                label=f"{contract.title or 'Statement of work'} v{contract.version}",
                sublabel=(
                    f"{contract.order.organisation.name} · "
                    f"{contract.order.reference} · {contract.get_status_display()}"
                ),
                href=f"/orders/{contract.order.reference}",
            )
        )

    # ── what we run ─────────────────────────────────────────────────────────
    for system in System.objects.filter(
        Q(name__icontains=q) | Q(slug__icontains=q) | Q(purpose__icontains=q)
    )[:PER_TYPE]:
        hits.append(
            Hit(
                kind="System",
                label=system.name,
                sublabel=system.purpose,
                href="/systems",
            )
        )

    for arrangement in (
        HostingArrangement.objects.filter(
            Q(identifier__icontains=q) | Q(provider__icontains=q)
        )
        .filter(retired_at__isnull=True)
        .select_related("organisation")[:PER_TYPE]
    ):
        hits.append(
            Hit(
                kind="Hosting",
                label=arrangement.identifier,
                sublabel=(
                    f"{arrangement.organisation.name} · "
                    f"{arrangement.get_kind_display()}"
                ),
                href=f"/clients/{arrangement.organisation_id}",
                exact=_matches_reference(arrangement.identifier, q),
            )
        )

    # ── people ──────────────────────────────────────────────────────────────
    for person in User.objects.filter(
        Q(full_name__icontains=q) | Q(email__icontains=q), is_staff=True
    )[:PER_TYPE]:
        hits.append(
            Hit(
                kind="Colleague",
                label=person.full_name or person.email,
                sublabel=person.get_staff_role_display() or "No role set",
                href="/team",
            )
        )

    # Reference matches first, then alphabetically inside that so the order is
    # stable between keystrokes — a list that reshuffles as you type is one you
    # cannot click.
    hits.sort(key=lambda hit: (not hit.exact, hit.kind, hit.label.lower()))
    if kind:
        hits = [hit for hit in hits if hit.kind == kind]
    return [hit.as_dict() for hit in hits]


def counts(q: str) -> list[dict]:
    """
    How many of each kind matched, for the filter row.

    ── COMPUTED FROM THE UNFILTERED RESULTS, ALWAYS ────────────────────────────

    The row has to show every kind's count even while one of them is selected,
    or picking "Order" makes the other filters read as zero and the way back
    disappears. So this is deliberately not aware of the current filter.

    ── AND IT COUNTS WHAT IS SHOWN, NOT WHAT EXISTS ────────────────────────────

    Per-type results are capped at PER_TYPE. A count of 40 above a list of 5 is
    a promise the list does not keep, and somebody would spend a while looking
    for the other 35.
    """
    found = search(q)
    tally = {name: 0 for name in KINDS}
    for hit in found:
        tally[hit["kind"]] = tally.get(hit["kind"], 0) + 1
    return [
        {"kind": name, "count": tally[name]} for name in KINDS if tally.get(name)
    ]
