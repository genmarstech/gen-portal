"""
Asking a founder for permission to do one thing, once.

═══════════════════════════════════════════════════════════════════════════════
WHAT MAY BE ASKED FOR IS AN ALLOWLIST, AND THE OMISSIONS ARE THE DESIGN.

`DELEGABLE` below is short. Anything not in it cannot be requested at all — the
refusal says a founder has to do it, and there is no button that turns it into
a message.

The four kinds of act deliberately left out:

  · CHANGING A STAFF ROLE. It grants every other permission, including to
    itself. A request to become a founder is exactly what an attacker holding
    a delivery account would send: reasonable-looking, urgent, arriving while
    the real founder is on a phone. One distracted tap and the permission model
    is gone, with an approval record making it look deliberate.

  · INVITING OR DEACTIVATING STAFF. Same reason one step removed — it creates
    the account that then asks for the role.

  · EDITING THE COMPANY'S BILLING DETAILS. Changing the paybill redirects every
    future invoice from a document that still looks entirely correct. There is
    no version of that which should arrive as a notification somebody clears
    while distracted.

  · GRANTING A PERSON ACCESS TO A CLIENT. It hands over another company's
    commercial detail, and the person harmed is not in the conversation.

Everything in the list is an act that is consequential but reversible, and
whose damage is visible to the whole team on a screen they already read.
═══════════════════════════════════════════════════════════════════════════════

AN APPROVAL IS ONE ACT, ONCE, WITHIN A WINDOW. It never changes what somebody
may do — see AccessRequest. `consume` is what spends it, and it is called at
the point of the write, not at the point of asking.
"""

from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from accounts.models import User
from portal.models import AccessRequest, ActivityLog


class Delegable:
    """One act a founder may hand over for a single use."""

    def __init__(self, key: str, label: str, held_by: str, note: str = ""):
        self.key = key
        self.label = label
        # Which standing permission this stands in for, in words, so the
        # founder deciding can see what they are lending rather than a slug.
        self.held_by = held_by
        self.note = note


DELEGABLE: dict[str, Delegable] = {
    d.key: d
    for d in [
        Delegable(
            "enquiry.convert",
            "Turn an enquiry into an order",
            "Commercial",
            "Commits the company's capacity. Reversible — the order can be closed.",
        ),
        Delegable(
            "offer.send",
            "Send a quote to a client",
            "Commercial",
            "The price becomes ours to honour until it expires. Withdrawable.",
        ),
        Delegable(
            "contract.issue",
            "Issue a statement of work",
            "Commercial",
            "Voidable, and a new version supersedes it.",
        ),
        Delegable(
            "tier.price",
            "Change a published tier price",
            "Commercial",
            "Affects future quotes only — sent offers are frozen.",
        ),
        Delegable(
            "client.archive",
            "Archive a client",
            "Founder",
            "Hides them from the working screens. Deletes nothing and can be undone.",
        ),
        Delegable(
            "client.delete",
            "Delete a client with nothing attached",
            "Founder",
            "Refused by the server the moment anything at all is attached.",
        ),
    ]
}


def may_request(action: str) -> bool:
    return action in DELEGABLE


@transaction.atomic
def request_permission(
    *, actor: User, action: str, subject: str = "", reason: str
) -> AccessRequest:
    """
    Ask a founder. Returns the pending request.

    The reason is required and the refusal says why: without it the founder is
    being asked to approve a verb, and the only honest answers to that are yes
    to everything or no to everything.
    """
    from .services import OperationsError, record

    if not may_request(action):
        raise OperationsError(
            "That is not something a founder can hand over, even once. Changing "
            "who has access, and changing where money is paid, stay with the "
            "person the charter makes accountable for them."
        )

    reason = (reason or "").strip()
    if not reason:
        raise OperationsError(
            "Say why you need it. Without it a founder is being asked to "
            "approve a verb rather than a decision.",
            field="reason",
        )

    existing = AccessRequest.objects.filter(
        requested_by=actor,
        action=action,
        subject=subject,
        status=AccessRequest.Status.PENDING,
    ).first()
    if existing is not None:
        # Not an error. Asking twice is what somebody does when they have not
        # heard back, and a second row would just make the queue noisier.
        return existing

    entry = AccessRequest.objects.create(
        requested_by=actor,
        requested_by_label=actor.full_name or actor.email,
        action=action,
        subject=subject[:120],
        reason=reason,
    )

    record(
        actor=actor,
        action=ActivityLog.Action.ACCESS_REQUESTED,
        subject=subject or action,
        summary=(
            f"{entry.requested_by_label} asked to {DELEGABLE[action].label.lower()}"
            + (f" — {subject}" if subject else "")
        ),
        requested=action,
    )
    return entry


@transaction.atomic
def decide(
    *, entry: AccessRequest, actor: User, approve: bool, note: str = ""
) -> AccessRequest:
    """
    A founder answers.

    Approving does not change what the requester may do. It authorises the one
    act they asked about, on the subject they named, once, for AccessRequest
    .LIFETIME — see that model.
    """
    from .services import OperationsError, record

    if not actor.can_manage_access:
        raise OperationsError("Only a founder can answer a permission request.")
    if not entry.is_open:
        raise OperationsError(
            f"That request was already {entry.get_status_display().lower()}."
        )
    if entry.requested_by_id == actor.id:
        # Otherwise a founder who is also the requester approves themselves,
        # which produces an audit trail that reads like oversight and is not.
        raise OperationsError(
            "You do not need to approve your own request — you can do it "
            "directly. Withdraw this instead."
        )

    entry.status = (
        AccessRequest.Status.APPROVED if approve else AccessRequest.Status.DECLINED
    )
    entry.decided_by = actor
    entry.decided_by_label = actor.full_name or actor.email
    entry.decided_at = timezone.now()
    entry.decision_note = (note or "").strip()
    if approve:
        entry.expires_at = timezone.now() + AccessRequest.LIFETIME
    entry.save(
        update_fields=[
            "status", "decided_by", "decided_by_label", "decided_at",
            "decision_note", "expires_at",
        ]
    )

    record(
        actor=actor,
        action=(
            ActivityLog.Action.ACCESS_APPROVED
            if approve
            else ActivityLog.Action.ACCESS_DECLINED
        ),
        subject=entry.subject or entry.action,
        summary=(
            f"{entry.requested_by_label} was "
            f"{'allowed' if approve else 'refused'} to "
            f"{DELEGABLE.get(entry.action).label.lower() if entry.action in DELEGABLE else entry.action}"
            + (f" — {entry.decision_note}" if entry.decision_note else "")
        ),
        single_use=approve,
    )
    return entry


@transaction.atomic
def mark_done_by_founder(*, entry: AccessRequest, actor: User, note: str = "") -> AccessRequest:
    """
    The founder did it themselves instead of handing the permission over.

    A separate outcome from APPROVED on purpose. "I did this for you" and "you
    may do this" are different decisions, and a log that recorded both as an
    approval would suggest a permission was lent when none was.
    """
    from .services import OperationsError, record

    if not actor.can_manage_access:
        raise OperationsError("Only a founder can answer a permission request.")
    if not entry.is_open:
        raise OperationsError(
            f"That request was already {entry.get_status_display().lower()}."
        )

    entry.status = AccessRequest.Status.DONE_BY_FOUNDER
    entry.decided_by = actor
    entry.decided_by_label = actor.full_name or actor.email
    entry.decided_at = timezone.now()
    entry.decision_note = (note or "").strip()
    entry.save(
        update_fields=[
            "status", "decided_by", "decided_by_label", "decided_at", "decision_note"
        ]
    )

    record(
        actor=actor,
        action=ActivityLog.Action.ACCESS_DECLINED,
        subject=entry.subject or entry.action,
        summary=(
            f"{entry.decided_by_label} handled it themselves rather than granting "
            f"{entry.requested_by_label} permission"
            + (f" — {entry.decision_note}" if entry.decision_note else "")
        ),
    )
    return entry


@transaction.atomic
def withdraw(*, entry: AccessRequest, actor: User) -> AccessRequest:
    """The requester no longer needs it."""
    from .services import OperationsError

    if entry.requested_by_id != actor.id:
        raise OperationsError("That is not your request.")
    if not entry.is_open:
        raise OperationsError("That request has already been answered.")

    entry.status = AccessRequest.Status.WITHDRAWN
    entry.save(update_fields=["status"])
    return entry


def consume(*, actor: User, action: str, subject: str = "") -> bool:
    """
    Spend a live approval, if this person holds one for exactly this act.

    ══════════════════════════════════════════════════════════════════════════
    CALLED AT THE POINT OF THE WRITE, NOT AT THE POINT OF ASKING.

    And it MARKS THE ROW USED before returning True. That ordering is the whole
    guarantee: an approval that were merely read would authorise the act every
    time the endpoint was called, which is a standing permission wearing the
    costume of a one-off.

    `select_for_update` because two tabs firing the same request at once is the
    ordinary way a single-use token gets spent twice.
    ══════════════════════════════════════════════════════════════════════════
    """
    from .services import record

    entry = (
        AccessRequest.objects.select_for_update()
        .filter(
            requested_by=actor,
            action=action,
            subject=subject,
            status=AccessRequest.Status.APPROVED,
            used_at__isnull=True,
            expires_at__gt=timezone.now(),
        )
        .order_by("expires_at")
        .first()
    )
    if entry is None:
        return False

    entry.used_at = timezone.now()
    entry.save(update_fields=["used_at"])

    record(
        actor=actor,
        action=ActivityLog.Action.ACCESS_USED,
        subject=entry.subject or entry.action,
        summary=(
            f"{entry.requested_by_label} used the permission "
            f"{entry.decided_by_label} granted: "
            f"{DELEGABLE[action].label.lower() if action in DELEGABLE else action}"
        ),
    )
    return True


def pending_for_founder():
    """The queue, oldest first — somebody is blocked on each of these."""
    return AccessRequest.objects.filter(
        status=AccessRequest.Status.PENDING
    ).select_related("requested_by").order_by("created_at")


def describe(entry: AccessRequest) -> dict:
    """The registry entry behind a request, for the screen that renders it."""
    known = DELEGABLE.get(entry.action)
    return {
        "label": known.label if known else entry.action,
        "held_by": known.held_by if known else "",
        "note": known.note if known else "",
    }
