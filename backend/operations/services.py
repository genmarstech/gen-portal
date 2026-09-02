"""
Operations write paths.

Reads can be a queryset. Writes here create commitments — an order is a fixed
scope at a fixed price (Charter 05 §I), a published note is something the client
is entitled to rely on — so they live in named functions with the rules in one
place, rather than spread across view bodies where the next view forgets one.
"""

from __future__ import annotations

import logging
from datetime import date

from django.db import IntegrityError, transaction
from django.utils import timezone

from accounts import emails, identity
from accounts.models import EmailCode, Membership, Organisation, User
from portal.models import Blocker, DeliveryGate, Enquiry, Order, ProgressNote

log = logging.getLogger(__name__)

REFERENCE_PREFIX = "GM"
_MAX_REFERENCE_ATTEMPTS = 10


class OperationsError(Exception):
    """A refusal the client of this module is expected to render, not a bug."""

    def __init__(self, message: str, field: str | None = None):
        super().__init__(message)
        self.message = message
        self.field = field


def next_reference(today: date | None = None) -> str:
    """
    The next order reference, e.g. GM-2026-0007.

    Derived from a count rather than a database sequence because it has to be
    per-year and human-readable; the caller retries on collision (see
    `convert_enquiry`), which is what makes it safe under concurrency. A
    sequence would be race-free but would leak gaps on rollback and could not
    restart each January.
    """
    year = (today or timezone.localdate()).year
    prefix = f"{REFERENCE_PREFIX}-{year}-"
    used = Order.objects.filter(reference__startswith=prefix).count()
    return f"{prefix}{used + 1:04d}"


@transaction.atomic
def convert_enquiry(
    *,
    enquiry: Enquiry,
    actor: User,
    title: str,
    scope: str,
    exclusions: str = "",
    contact: User | None = None,
    target_date: date | None = None,
) -> Order:
    """
    Turn a qualified enquiry into an order.

    This is the moment the company commits, so the guards are here rather than
    in the view:

      · An enquiry converts ONCE. Converting twice would give one client two
        orders for one piece of work and two sets of milestones to be invoiced
        against.
      · A declined enquiry does not convert. If the decision changed, reopen it
        deliberately — silently resurrecting a decline loses the fact that
        somebody said no.
      · Scope is required and exclusions are not. Charter 05 §I requires
        exclusions in writing BEFORE work begins, but an empty exclusions field
        at conversion is honest ("we have not yet said what is out of scope")
        whereas an empty scope is not an order at all.
      · The named contact must be staff. `Order.contact` is the client's named
        point of contact under Charter 05 §I; pointing it at a client account
        would make the client their own escalation path.

    Atomic in full: an order created without the enquiry being marked converted
    is the double-conversion bug waiting one refresh.
    """
    if enquiry.converted_to_id is not None:
        raise OperationsError(
            f"This enquiry is already order {enquiry.converted_to.reference}."
        )
    if enquiry.status == Enquiry.Status.DECLINED:
        raise OperationsError(
            "This enquiry was declined. Move it back to qualifying first if that "
            "has changed."
        )
    if not scope.strip():
        raise OperationsError(
            "An order needs a scope. Charter 05 §I — fixed scope, in writing.",
            field="scope",
        )
    if not title.strip():
        raise OperationsError("Give the order a title.", field="title")

    lead = contact or actor
    if not lead.is_staff:
        raise OperationsError(
            "The named contact must be a Genmars account.", field="contact"
        )

    # Retry on the unique constraint rather than locking the table: two people
    # converting at the same moment both compute the same reference, and one of
    # them loses the race. Losing it costs a re-count, not the conversion.
    last_error: IntegrityError | None = None
    for _ in range(_MAX_REFERENCE_ATTEMPTS):
        try:
            with transaction.atomic():
                order = Order.objects.create(
                    organisation=enquiry.organisation,
                    reference=next_reference(),
                    title=title.strip(),
                    scope=scope.strip(),
                    exclusions=exclusions.strip(),
                    status=Order.Status.SCOPING,
                    contact=lead,
                    target_date=target_date,
                )
            break
        except IntegrityError as exc:  # pragma: no cover - needs a real race
            last_error = exc
    else:  # pragma: no cover - ten collisions in a row is a broken assumption
        raise OperationsError(
            "Could not allocate an order reference. Try again."
        ) from last_error

    create_delivery_gates(order=order)

    enquiry.converted_to = order
    enquiry.status = Enquiry.Status.CONVERTED
    enquiry.decided_by = actor
    enquiry.decided_at = timezone.now()
    enquiry.save(
        update_fields=["converted_to", "status", "decided_by", "decided_at"]
    )
    return order


@transaction.atomic
def decide_enquiry(
    *, enquiry: Enquiry, actor: User, status: str, note: str = ""
) -> Enquiry:
    """
    Move an enquiry through triage without converting it.

    CONVERTED is not settable here — it is a side effect of `convert_enquiry`
    and nothing else. Allowing it would let someone mark an enquiry converted
    with no order behind it, which is the dead end `converted_to` was added to
    close.
    """
    allowed = {
        Enquiry.Status.NEW,
        Enquiry.Status.QUALIFYING,
        Enquiry.Status.DECLINED,
    }
    if status not in allowed:
        raise OperationsError(
            "An enquiry becomes converted by creating its order, not by being "
            "marked converted.",
            field="status",
        )
    if enquiry.converted_to_id is not None:
        raise OperationsError(
            f"This enquiry is order {enquiry.converted_to.reference}. Its status "
            "follows the order now."
        )
    if status == Enquiry.Status.DECLINED and not note.strip():
        raise OperationsError(
            "Say why. A decline nobody can explain in six months is not a "
            "decision, it is a gap in the record.",
            field="outcome_note",
        )

    enquiry.status = status
    if note.strip():
        enquiry.outcome_note = note.strip()
    enquiry.decided_by = actor
    enquiry.decided_at = timezone.now()
    enquiry.save(
        update_fields=["status", "outcome_note", "decided_by", "decided_at"]
    )
    return enquiry


def publish_note(*, note: ProgressNote) -> ProgressNote:
    """
    Publish a drafted weekly note, and tell the client it exists.

    Separate from writing it, because publishing is the irreversible half: the
    model refuses to let a published note's body change (Charter 05 §III — the
    client is entitled to rely on what they were told), so a draft is the only
    chance to fix a typo.

    ── PUBLISHING COMMITS EVEN IF THE EMAIL FAILS ─────────────────────────────
    The note is published the moment it is saved; the client can read it in the
    portal whether or not Resend is reachable. Rolling the publish back because
    mail failed would mean an outage at our mail provider silently un-publishes
    a promise we already made. So the send is attempted after, and a failure is
    logged loudly rather than raised — the note is out, and the worst case is
    that someone has to sign in to find it.
    """
    if note.is_published:
        return note
    note.published_at = timezone.now()
    note.save(update_fields=["published_at"])

    _notify_progress_note(note)
    return note


def _notify_progress_note(note: ProgressNote) -> None:
    """
    Email everyone at the client who wants updates.

    Deliberately NOT every member: `receives_updates` exists because a second
    or third person at a client often does not want every note, and service
    mail with no way to stop it becomes marketing in the recipient's mind.

    Unverified addresses are skipped. An invited account that never accepted is
    an address nobody has proved they read, and sending a client's commercial
    detail to it would be sending it to whoever happens to own that mailbox.
    """
    order = note.order
    recipients = (
        Membership.objects.filter(
            organisation=order.organisation, receives_updates=True
        )
        .select_related("user")
        .exclude(user__email_verified_at__isnull=True)
    )

    for membership in recipients:
        try:
            emails.send_progress_note(
                email=membership.user.email,
                reference=order.reference,
                title=order.title,
                week_of=note.week_of.isoformat(),
                body=note.body,
            )
        except Exception:
            # Never let one bad address stop the rest, and never let mail
            # failure surface as "publishing failed" — it did not.
            log.exception(
                "progress note %s published but not emailed to membership %s",
                note.pk,
                membership.pk,
            )


# ── engineering delivery ─────────────────────────────────────────────────────


def create_delivery_gates(*, order: Order) -> list[DeliveryGate]:
    """
    Give an order its six definition-of-done gates.

    Created WITH the order, not when someone remembers. Charter 03 §II is the
    standard every engagement is held to, so a gate list that has to be opted
    into is a standard that applies only to the orders somebody thought about.

    Idempotent: get_or_create, so running it against an existing order backfills
    without duplicating. That matters because orders created before this existed
    have no gates and still need them.

    The label is COPIED from the choice at creation time. If the definition of
    done is reworded later, work already delivered keeps showing the standard it
    was actually held to.
    """
    gates = []
    for position, (value, label) in enumerate(DeliveryGate.Gate.choices, start=1):
        gate, _ = DeliveryGate.objects.get_or_create(
            order=order,
            gate=value,
            defaults={"label": label, "position": position},
        )
        gates.append(gate)
    return gates


@transaction.atomic
def set_gate(
    *, gate: DeliveryGate, actor: User, met: bool, note: str = ""
) -> DeliveryGate:
    """
    Mark a definition-of-done gate met, or put it back.

    A NOTE IS REQUIRED TO MEET ONE. "Automated tests cover the critical paths"
    ticked with nothing beside it is an opinion; with "portal/tests/ +
    operations/tests/, 148 green in CI" beside it, it is a record somebody can
    check. Charter 03 §II is only worth anything if the six can be audited
    afterwards, and a bare tick cannot be.

    Un-meeting is deliberately allowed and deliberately keeps the note. Work
    regresses — a test starts failing, a runbook goes stale — and a gate that
    could only ever be ticked would make the checklist a ratchet that always
    reads complete.
    """
    if met and not note.strip() and not gate.note.strip():
        raise OperationsError(
            "Say how it was satisfied. A tick nobody can check is not evidence.",
            field="note",
        )

    if note.strip():
        gate.note = note.strip()

    if met:
        gate.met_at = gate.met_at or timezone.now()
        gate.met_by = gate.met_by or actor
    else:
        gate.met_at = None
        gate.met_by = None

    gate.save(update_fields=["met_at", "met_by", "note"])
    return gate


@transaction.atomic
def raise_blocker(
    *, order: Order, actor: User, summary: str, detail: str = "", waiting_on: str
) -> Blocker:
    if not summary.strip():
        raise OperationsError("Say what is blocked.", field="summary")
    if waiting_on not in Blocker.WaitingOn.values:
        raise OperationsError("Who is it waiting on?", field="waiting_on")

    return Blocker.objects.create(
        order=order,
        summary=summary.strip(),
        detail=detail.strip(),
        waiting_on=waiting_on,
        raised_by=actor,
    )


@transaction.atomic
def clear_blocker(*, blocker: Blocker, resolution: str = "") -> Blocker:
    """
    Close a blocker.

    The resolution is optional but asked for, because the useful question three
    months later is rarely "was it blocked" — it is "what unblocked it", and
    that is the part nobody writes down unless there is a box for it.
    """
    if blocker.cleared_at is None:
        blocker.cleared_at = timezone.now()
    if resolution.strip():
        blocker.resolution = resolution.strip()
    blocker.save(update_fields=["cleared_at", "resolution"])
    return blocker


# ── client accounts ──────────────────────────────────────────────────────────


@transaction.atomic
def create_organisation(*, name: str) -> Organisation:
    """
    A client organisation, created by staff rather than by a signup.

    Onboarding already creates one for whoever signs up. This is for the other
    case: a client we talked to first, whose people we want to invite before
    any of them has been to the site.
    """
    name = (name or "").strip()
    if not name:
        raise OperationsError("Give the organisation a name.", field="name")

    existing = Organisation.objects.filter(name__iexact=name).first()
    if existing:
        raise OperationsError(
            f"{existing.name} already exists.", field="name"
        )
    return Organisation.objects.create(name=name)


@transaction.atomic
def invite_to_organisation(
    *,
    organisation: Organisation,
    actor: User,
    email: str,
    full_name: str = "",
    role: str = Membership.Role.MEMBER,
) -> tuple[Membership, bool]:
    """
    Give somebody access to a client organisation. Returns (membership, invited).

    ── THE ACCOUNT IS CREATED WITHOUT A USABLE PASSWORD ────────────────────────
    See identity.accept_invite. Staff never know a client credential, it never
    travels by email, and until the person sets one nobody can sign in as them
    — including us. `invited` is False when the account already existed, which
    is the case where no code is sent because they already have a password.

    ── A CLIENT ACCOUNT IS NEVER STAFF ─────────────────────────────────────────
    Refuses outright if the address belongs to a Genmars account. `is_staff`
    grants nothing in the client portal by design (portal/selectors.py), but
    giving a staff account a client membership would hand it real client data
    through the client API — quietly, and through a path nobody would think to
    check.
    """
    email = (email or "").strip().lower()
    if not email:
        raise OperationsError("An email address is needed.", field="email")
    if role not in Membership.Role.values:
        raise OperationsError("Owner or member.", field="role")

    user = User.objects.filter(email=email).first()

    if user and user.is_staff:
        raise OperationsError(
            "That is a Genmars account. Staff do not hold client memberships.",
            field="email",
        )

    if Membership.objects.filter(user=user, organisation=organisation).exists():
        raise OperationsError(
            f"{email} is already on {organisation.name}.", field="email"
        )

    invited = False
    if user is None:
        user = User.objects.create_user(
            email=email, password=None, full_name=(full_name or "").strip()
        )
        # create_user with password=None already produces an unusable password;
        # this is belt and braces against that changing under us, because the
        # whole guarantee rests on it.
        user.set_unusable_password()
        user.save(update_fields=["password"])
        invited = True

    membership = Membership.objects.create(
        user=user, organisation=organisation, role=role, invited_by=actor
    )

    if invited:
        issued = identity.issue_code(user, EmailCode.Purpose.INVITE)
        emails.send_invite(
            email=user.email,
            code=issued.code,
            organisation=organisation.name,
            invited_by=actor.full_name or actor.email,
        )

    return membership, invited


@transaction.atomic
def update_membership(
    *, membership: Membership, role: str | None = None, receives_updates: bool | None = None
) -> Membership:
    fields = []
    if role is not None:
        if role not in Membership.Role.values:
            raise OperationsError("Owner or member.", field="role")
        membership.role = role
        fields.append("role")
    if receives_updates is not None:
        membership.receives_updates = receives_updates
        fields.append("receives_updates")
    if fields:
        membership.save(update_fields=fields)
    return membership


@transaction.atomic
def remove_membership(*, membership: Membership) -> None:
    """
    Revoke access.

    The MEMBERSHIP goes, not the account. Deleting the user would cascade to
    anything they submitted — the enquiry that started the engagement, for one —
    and losing the record of why we took work on is a worse outcome than a
    dormant account. Without a membership they can sign in and see nothing,
    which is what revoked access should look like.
    """
    membership.delete()
