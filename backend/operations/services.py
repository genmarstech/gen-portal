"""
Operations write paths.

Reads can be a queryset. Writes here create commitments — an order is a fixed
scope at a fixed price (Charter 05 §I), a published note is something the client
is entitled to rely on — so they live in named functions with the rules in one
place, rather than spread across view bodies where the next view forgets one.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from decimal import Decimal

from django.conf import settings
from django.db import IntegrityError, models, transaction
from django.utils import timezone

from accounts import emails, identity
from accounts.models import EmailCode, Membership, Organisation, User
from portal import mpesa
from portal.models import (
    BillingProfile,
    Blocker,
    ClientProfile,
    ContactAttachment,
    ContactLogEntry,
    Contract,
    Decision,
    DeliveryGate,
    HostingArrangement,
    ActivityLog,
    Enquiry,
    Incident,
    Invoice,
    Milestone,
    MpesaPayment,
    Notification,
    Offer,
    Order,
    PaymentRecord,
    ProgressNote,
    Service,
    ServiceTier,
    Shift,
    SupportMessage,
    SupportTicket,
    Task,
)

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
    service: Service | None = None,
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
    # Pre-fill from the service before validating, so picking one is enough to
    # satisfy the scope requirement. The wording is a STARTING POINT — it lands
    # on the order and is edited there, and the contract snapshots the edited
    # version rather than the catalogue.
    if service is not None:
        scope = scope.strip() or service.default_scope
        exclusions = exclusions.strip() or service.default_exclusions

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
                    service=service,
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
def create_organisation(*, name: str, actor: User | None = None) -> Organisation:
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
    organisation = Organisation.objects.create(name=name)
    if actor is not None:
        record(
            actor=actor,
            action=ActivityLog.Action.CLIENT_CREATED,
            subject=name,
            organisation=organisation,
            summary=f"{name} added as a client",
        )
    return organisation


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


# ── services catalogue ───────────────────────────────────────────────────────


@transaction.atomic
def upsert_service(
    *,
    service: Service | None = None,
    name: str,
    summary: str = "",
    default_scope: str = "",
    default_exclusions: str = "",
    default_deliverables: str = "",
    is_active: bool = True,
) -> Service:
    """
    Create or edit an offering.

    Editing changes the STARTING POINT for future orders and nothing else.
    Orders already created keep the wording they were given, and contracts keep
    their snapshot — so improving a service's exclusions cannot retroactively
    change what a client agreed to.
    """
    from django.utils.text import slugify

    name = (name or "").strip()
    if not name:
        raise OperationsError("Give the service a name.", field="name")

    clash = Service.objects.filter(name__iexact=name)
    if service:
        clash = clash.exclude(pk=service.pk)
    if clash.exists():
        raise OperationsError(f"{name} already exists.", field="name")

    if service is None:
        service = Service(slug=slugify(name)[:120])

    service.name = name
    service.summary = (summary or "").strip()
    service.default_scope = (default_scope or "").strip()
    service.default_exclusions = (default_exclusions or "").strip()
    service.default_deliverables = (default_deliverables or "").strip()
    service.is_active = is_active
    service.save()
    return service


# ── contracts ────────────────────────────────────────────────────────────────


def _payment_terms(order: Order) -> tuple[str, object]:
    """
    The milestones as text, and their total.

    Rendered to text at snapshot time rather than pointed at, because a
    contract that references live milestone rows changes when somebody edits a
    milestone — which is the thing a contract exists to prevent.
    """
    from decimal import Decimal

    lines: list[str] = []
    total = Decimal("0")
    for m in order.milestones.order_by("position", "due_on"):
        due = f", due {m.due_on.isoformat()}" if m.due_on else ""
        lines.append(f"{m.name}: KES {m.amount_kes}{due}")
        total += m.amount_kes
    return "\n".join(lines), total


@transaction.atomic
def issue_contract(*, order: Order, actor: User, deliverables: str = "") -> Contract:
    """
    Freeze what has been agreed, as a new version.

    ── WHAT ISSUING ACTUALLY DOES ──────────────────────────────────────────────
    Copies the scope, exclusions, deliverables, milestone terms and total as
    they stand RIGHT NOW into a row that nothing afterwards edits. Editing the
    order later does not touch an issued contract, which is the whole point —
    see the note on the model.

    Any previous live version is superseded rather than deleted. What was agreed
    in March stays readable in September; that is the only reason anyone keeps
    contracts at all.

    ── WHY SCOPE IS REQUIRED AND EXCLUSIONS ARE NOT ───────────────────────────
    An order with no scope is not something anyone can agree to. Empty
    exclusions are honest — "we have not yet said what is out" — and Charter 05
    §I asks for them in writing before work BEGINS, which is a conversation this
    document starts rather than one it has to have finished.
    """
    if not order.scope.strip():
        raise OperationsError(
            "This order has no scope. A contract needs something to agree to.",
            field="scope",
        )

    Contract.objects.filter(
        order=order, status__in=[Contract.Status.ISSUED, Contract.Status.SIGNED]
    ).update(status=Contract.Status.SUPERSEDED)

    terms, total = _payment_terms(order)
    version = (
        Contract.objects.filter(order=order).aggregate(models.Max("version"))[
            "version__max"
        ]
        or 0
    ) + 1

    text = (deliverables or "").strip()
    if not text and order.service:
        text = order.service.default_deliverables

    return Contract.objects.create(
        order=order,
        version=version,
        title=order.title,
        scope=order.scope,
        exclusions=order.exclusions,
        deliverables=text,
        total_kes=total,
        payment_terms=terms,
        target_date=order.target_date,
        status=Contract.Status.ISSUED,
        issued_at=timezone.now(),
        issued_by=actor,
    )


@transaction.atomic
def record_signature(
    *,
    contract: Contract,
    actor: User,
    signed_on,
    signed_by_name: str,
    note: str = "",
) -> Contract:
    """
    Record that the client signed — somewhere else.

    ── THIS IS NOT AN E-SIGNATURE, AND MUST NOT LOOK LIKE ONE ─────────────────
    Genmars runs no signing product. What happened is that somebody signed a
    PDF, or replied "agreed" to an email, and a member of staff is writing that
    down. Charter 04 §IV forbids claiming a capability we do not have, and a
    field called "signature" on a screen a client never touched would be exactly
    that claim.

    So a name and a date are required — a record with neither is not evidence of
    anything — and `recorded_by` names the person at Genmars who asserted it,
    because that is the fact this row actually establishes.
    """
    if contract.status not in {Contract.Status.ISSUED, Contract.Status.SIGNED}:
        raise OperationsError(
            "Only an issued contract can be signed. Issue it first, or issue a "
            "new version if this one was superseded."
        )
    if not signed_by_name.strip():
        raise OperationsError("Who signed it?", field="signed_by_name")
    if signed_on is None:
        raise OperationsError("When was it signed?", field="signed_on")

    contract.status = Contract.Status.SIGNED
    contract.signed_on = signed_on
    contract.signed_by_name = signed_by_name.strip()
    contract.signature_note = (note or "").strip()
    contract.recorded_by = actor
    contract.save(
        update_fields=[
            "status",
            "signed_on",
            "signed_by_name",
            "signature_note",
            "recorded_by",
        ]
    )
    return contract


@transaction.atomic
def void_contract(*, contract: Contract, reason: str) -> Contract:
    """
    Withdraw a contract that should not have been issued.

    Voiding keeps the row. A contract that vanishes is a contract nobody can
    prove was withdrawn rather than never sent, and the reason is required for
    the same reason a decline needs one.
    """
    if not reason.strip():
        raise OperationsError("Say why it is being voided.", field="reason")
    if contract.status == Contract.Status.SIGNED:
        raise OperationsError(
            "This one is signed. A signed contract is ended by agreement, not "
            "by voiding it here — issue a superseding version instead."
        )
    contract.status = Contract.Status.VOID
    contract.signature_note = reason.strip()
    contract.save(update_fields=["status", "signature_note"])
    return contract


# ── the team ─────────────────────────────────────────────────────────────────


def _founders(exclude: User | None = None) -> "models.QuerySet[User]":
    qs = User.objects.filter(
        is_staff=True, is_active=True, staff_role=User.StaffRole.FOUNDER
    )
    return qs.exclude(pk=exclude.pk) if exclude else qs


@transaction.atomic
def invite_staff(
    *, actor: User, email: str, full_name: str = "", role: str
) -> tuple[User, bool]:
    """
    Add somebody to Genmars. Returns (user, invited).

    Same guarantee as a client invite: the account is created with an UNUSABLE
    password and nobody can sign in as them until they set one. That matters
    more here, not less — this account can read every client's commercial
    detail.

    ── A CLIENT ADDRESS CANNOT BECOME STAFF ────────────────────────────────────
    Refused outright. Promoting an existing client account would give it
    is_staff while it still holds Memberships, so it would read every
    organisation through operations AND appear as a client of one. Genmars
    staff and Genmars clients are different people; if somebody genuinely is
    both, they get two accounts and the boundary stays legible.
    """
    email = (email or "").strip().lower()
    if not email:
        raise OperationsError("An email address is needed.", field="email")
    if role not in User.StaffRole.values:
        raise OperationsError("Founder, commercial, or delivery.", field="role")

    user = User.objects.filter(email=email).first()

    if user and not user.is_staff:
        raise OperationsError(
            "That address is a client account. Staff and clients are different "
            "people — use a Genmars address.",
            field="email",
        )
    if user and user.is_staff:
        raise OperationsError(f"{email} is already on the team.", field="email")

    user = User.objects.create_user(
        email=email, password=None, full_name=(full_name or "").strip()
    )
    user.is_staff = True
    user.staff_role = role
    user.set_unusable_password()
    user.save(update_fields=["is_staff", "staff_role", "password"])

    issued = identity.issue_code(user, EmailCode.Purpose.INVITE)
    emails.send_staff_invite(
        email=user.email,
        code=issued.code,
        role=user.get_staff_role_display(),
        invited_by=actor.full_name or actor.email,
    )
    return user, True


@transaction.atomic
def set_staff_role(*, actor: User, user: User, role: str) -> User:
    """
    Change what somebody may do.

    ── THE LAST FOUNDER CANNOT BE DEMOTED ──────────────────────────────────────
    `can_manage_access` is founder-only, so removing the last one leaves a
    system nobody can grant anything in — recoverable only by a shell on the
    production box. A permission model that can strand itself is worse than no
    permission model, because it fails at the moment somebody is already having
    a bad day.
    """
    if not user.is_staff:
        raise OperationsError("That is not a Genmars account.")
    if role not in User.StaffRole.values:
        raise OperationsError("Founder, commercial, or delivery.", field="role")

    losing_founder = (
        user.staff_role == User.StaffRole.FOUNDER and role != User.StaffRole.FOUNDER
    )
    if losing_founder and not _founders(exclude=user).exists():
        raise OperationsError(
            "This is the only founder. Make somebody else a founder first, or "
            "nobody will be able to change roles at all."
        )

    user.staff_role = role
    user.save(update_fields=["staff_role"])
    log.info(
        "staff role changed: %s -> %s by %s", user.email, role, actor.email
    )
    return user


@transaction.atomic
def set_staff_active(*, actor: User, user: User, active: bool) -> User:
    """
    Revoke or restore access without deleting the account.

    Django refuses to authenticate an inactive user, so this is a real
    revocation. The account stays because the person's authorship does — the
    progress notes they wrote, the gates they met, the contracts they issued.
    Deleting them would either destroy that record or fail on a PROTECT, and
    both are worse than a dormant row.

    You cannot deactivate yourself: it is never what was meant, and it ends
    with somebody locked out of the system they were tidying.
    """
    if not user.is_staff:
        raise OperationsError("That is not a Genmars account.")
    if user.pk == actor.pk and not active:
        raise OperationsError("You cannot deactivate your own account.")
    if (
        not active
        and user.staff_role == User.StaffRole.FOUNDER
        and not _founders(exclude=user).exists()
    ):
        raise OperationsError(
            "This is the only active founder. Make somebody else a founder first."
        )

    # Charter 05 §I — every live order names a contact the client can reach.
    # Revoking someone would silently leave those clients pointed at an account
    # that can no longer sign in. Refusing here forces the reassignment to
    # happen deliberately, and names the orders so it is one job rather than a
    # hunt.
    if not active:
        stranded = list(
            user.orders_as_contact.exclude(
                status__in=[Order.Status.DELIVERED, Order.Status.CLOSED]
            ).values_list("reference", flat=True)
        )
        if stranded:
            raise OperationsError(
                "They are still the named contact on "
                + ", ".join(stranded)
                + ". Name someone else on those orders first — the client is "
                "promised a contact they can reach."
            )

    user.is_active = active
    user.save(update_fields=["is_active"])
    log.info(
        "staff %s: %s by %s",
        "reactivated" if active else "deactivated",
        user.email,
        actor.email,
    )
    return user


# ── invoicing ────────────────────────────────────────────────────────────────




# ── the activity log ─────────────────────────────────────────────────────────


def record(
    *,
    actor: User | None,
    action: str,
    summary: str,
    subject: str = "",
    organisation: Organisation | None = None,
    **detail,
) -> None:
    """
    Write one line to the log. Append-only; see ActivityLog.

    NEVER RAISES INTO THE CALLER. An invoice that was issued correctly must not
    be rolled back because the log could not be written — the invoice is the
    fact, the log is the account of it. A failure here is logged to the journal
    and swallowed.

    NEVER PASS A SECRET IN `detail`. Verification codes, reset codes, keys,
    passkeys. It is a free-form JSON field written to disk and read by everyone
    with operations access, which is exactly the shape of the mistake.
    """
    try:
        ActivityLog.objects.create(
            actor=actor,
            actor_label=(actor.full_name or actor.email) if actor else "System",
            action=action,
            subject=subject[:120],
            summary=summary[:300],
            detail=detail,
            organisation=organisation,
        )
    except Exception:  # pragma: no cover - defensive, see the note above
        log.exception("could not write an activity entry for %s", action)


# ── notifications ────────────────────────────────────────────────────────────
#
# Deliberately small and deliberately dumb. A notification points at something
# that is already true and already visible elsewhere; it is never the only
# record of a fact, and never how a client is told something that matters.
# See Notification's docstring.
#
# Creation never raises into the caller. An invoice that was issued correctly
# must not be rolled back because a notification could not be written — the
# invoice is the thing that matters, and the notification is a convenience.


def _notify(
    *,
    users,
    audience: str,
    kind: str,
    title: str,
    body: str = "",
    url: str = "",
) -> None:
    """One row per person. See Notification's docstring for why."""
    rows = [
        Notification(
            user=user,
            audience=audience,
            kind=kind,
            title=title,
            body=body,
            url=url,
        )
        for user in users
    ]
    if not rows:
        return
    try:
        Notification.objects.bulk_create(rows)
    except Exception:  # pragma: no cover - defensive, see the note above
        log.exception("could not write %s notifications", kind)


def _client_recipients(organisation: Organisation):
    """
    Everyone on the client's account, and only people who can still sign in.

    A notification for a deactivated user is a row nobody will ever read.
    """
    return User.objects.filter(
        memberships__organisation=organisation,
        is_active=True,
    ).distinct()


def _staff_recipients():
    return User.objects.filter(is_staff=True, is_active=True)


def notify_invoice_issued(invoice: Invoice) -> None:
    _notify(
        users=_client_recipients(invoice.organisation),
        audience=Notification.Audience.CLIENT,
        kind=Notification.Kind.INVOICE_ISSUED,
        title=f"Invoice {invoice.number}",
        body=f"KES {invoice.amount_kes:,.2f} — {invoice.description}",
        url="/invoices",
    )


def notify_payment_recorded(
    invoice: Invoice, payment: PaymentRecord, *, settled: bool
) -> None:
    if settled:
        title = f"Invoice {invoice.number} paid"
        body = f"KES {invoice.amount_kes:,.2f} received in full. Thank you."
    else:
        title = f"Payment received against {invoice.number}"
        body = (
            f"KES {payment.amount_kes:,.2f} received. "
            f"KES {invoice.balance:,.2f} still outstanding."
        )

    _notify(
        users=_client_recipients(invoice.organisation),
        audience=Notification.Audience.CLIENT,
        kind=(
            Notification.Kind.INVOICE_PAID
            if settled
            else Notification.Kind.PAYMENT_RECORDED
        ),
        title=title,
        body=body,
        url="/invoices",
    )


def notify_invoice_voided(invoice: Invoice) -> None:
    _notify(
        users=_client_recipients(invoice.organisation),
        audience=Notification.Audience.CLIENT,
        kind=Notification.Kind.INVOICE_VOIDED,
        title=f"Invoice {invoice.number} withdrawn",
        body="It is no longer owed. Nothing is required from you.",
        url="/invoices",
    )


def notify_enquiry_received(enquiry: Enquiry) -> None:
    _notify(
        users=_staff_recipients(),
        audience=Notification.Audience.STAFF,
        kind=Notification.Kind.ENQUIRY_RECEIVED,
        title=f"New enquiry from {enquiry.organisation.name}",
        body=(enquiry.problem or "")[:200],
        url="/",
    )



def next_invoice_number(today: date | None = None) -> str:
    """
    The next invoice number, e.g. GM-INV-2026-0007.

    Counts EVERY invoice for the year including voided ones, because a voided
    invoice keeps its number forever. Reusing it would put two different
    documents in the world under one reference, which is the single thing an
    invoice number exists to prevent.
    """
    year = (today or timezone.localdate()).year
    prefix = f"{REFERENCE_PREFIX}-INV-{year}-"
    used = Invoice.objects.filter(number__startswith=prefix).count()
    return f"{prefix}{used + 1:04d}"


@transaction.atomic
def issue_invoice(
    *,
    order: Order,
    actor: User,
    milestone: Milestone | None = None,
    description: str = "",
    amount_kes: Decimal | None = None,
    due_on: date | None = None,
    issued_on: date | None = None,
) -> Invoice:
    """
    Ask a client for money.

    ── WHY THE GUARDS ARE HERE ────────────────────────────────────────────────

    Billing is the most consequential outward-facing act in this system. An
    invoice reaches a client's accounts department, gets paid, and turns up in
    two sets of books. Getting it wrong is not a UI bug, it is a conversation
    about money with someone who trusted us.

      · A SIGNED contract is required. Charter 02 §I is explicit that work
        begins when a SOW is signed, and billing for work that was never agreed
        in writing is asking someone to pay for something they did not buy.
        This is the guard most likely to be resented in a hurry and the one
        most worth keeping.

      · The amount must be positive. A zero invoice asks for nothing and a
        negative one is a credit note, which is a different document with
        different accounting treatment — not something to smuggle through here.

      · A milestone is billed ONCE. The second invoice for the same milestone
        is double-billing, and it looks exactly like a legitimate invoice to
        whoever receives it. A VOIDED one does not count, which is how a
        correction is made.

      · Amount and description are COPIED. See Invoice's docstring: an invoice
        that recalculates is not an invoice.
    """
    if not order.contracts.filter(status=Contract.Status.SIGNED).exists():
        raise OperationsError(
            "There is no signed statement of work on this order. Charter 02 §I — "
            "the agreement comes before the work, and before the bill.",
        )

    if milestone is not None:
        if milestone.order_id != order.pk:
            raise OperationsError("That milestone belongs to a different order.")
        clash = milestone.invoices.exclude(status=Invoice.Status.VOID).first()
        if clash is not None:
            raise OperationsError(
                f"{milestone.name} was already invoiced as {clash.number}. "
                "Void that invoice if it was wrong.",
            )
        description = description.strip() or milestone.name
        if amount_kes is None:
            amount_kes = milestone.amount_kes

    description = description.strip()
    if not description:
        raise OperationsError(
            "Say what this bills. The client's accounts department reads this "
            "line and nothing else.",
            field="description",
        )
    if amount_kes is None:
        raise OperationsError("An invoice needs an amount.", field="amount_kes")

    amount_kes = Decimal(amount_kes)
    if amount_kes <= 0:
        raise OperationsError(
            "An invoice must ask for a positive amount. A refund or a "
            "correction is a credit note, not an invoice.",
            field="amount_kes",
        )

    issued_on = issued_on or timezone.localdate()
    if due_on and due_on < issued_on:
        raise OperationsError(
            "That due date is before the invoice date, so it would arrive "
            "already overdue.",
            field="due_on",
        )

    # Same retry-on-collision as convert_enquiry: the number comes from a count,
    # so two invoices issued in the same instant can pick the same one.
    for attempt in range(5):
        try:
            with transaction.atomic():
                invoice = Invoice.objects.create(
                    number=next_invoice_number(issued_on),
                    organisation=order.organisation,
                    billed_to_name=order.organisation.name,
                    order=order,
                    milestone=milestone,
                    description=description,
                    amount_kes=amount_kes,
                    issued_on=issued_on,
                    due_on=due_on,
                    issued_by=actor,
                )
            break
        except IntegrityError:
            if attempt == 4:
                raise
    else:  # pragma: no cover - the loop always breaks or raises
        raise OperationsError("Could not allocate an invoice number.")

    if milestone is not None:
        milestone.status = Milestone.Status.INVOICED
        milestone.save(update_fields=["status"])

    notify_invoice_issued(invoice)
    record(
        actor=actor,
        action=ActivityLog.Action.INVOICE_ISSUED,
        subject=invoice.number,
        organisation=order.organisation,
        summary=f"{invoice.number} issued to {order.organisation.name} for KES {invoice.amount_kes:,.2f}",
        order=order.reference,
        amount_kes=str(invoice.amount_kes),
    )

    log.info("invoice %s issued for %s by %s", invoice.number, order.reference, actor.email)
    return invoice


@transaction.atomic
def issue_direct_invoice(
    *,
    organisation: Organisation,
    actor: User,
    description: str,
    amount_kes: Decimal,
    due_on: date | None = None,
    issued_on: date | None = None,
    outside_system: str = "",
) -> Invoice:
    """
    Bill a client for something that is not a project milestone.

    ── WHY THIS EXISTS ALONGSIDE issue_invoice ─────────────────────────────────

    issue_invoice refuses to bill without a signed statement of work, and that
    guard is right: Charter 02 §I puts the agreement before the work and before
    the bill, and billing project work that nobody signed for is asking someone
    to pay for something they did not buy.

    But that guard assumes every invoice bills a project, and not every one
    does. A past client asks for an afternoon's work. A licence renews. A
    domain gets paid for on their behalf. Forcing those through a fabricated
    order would put rows in the delivery pipeline for work that has no
    pipeline, and would make the order board a worse description of reality
    every time it happened.

    So this is a SEPARATE door with its OWN guard rather than a flag that
    switches the other one off — a flag would eventually get passed on a
    project invoice by someone in a hurry, and the contract requirement would
    quietly stop existing.

    ── THE GUARD HERE, AND WHY IT WAS WIDENED ──────────────────────────────────

    The client must be one we have an actual relationship with. Anyone can be
    typed into the organisations list; that is not the same as somebody we may
    send a bill to, and the failure this prevents is invoicing a duplicate or a
    half-finished record by mistake.

    It used to accept only an order or a previous invoice as evidence. That was
    too narrow and it blocked the commonest real case: a client Genmars has
    worked with for a year whose engagement predates this system, so the
    evidence of the relationship is real and simply lives somewhere else.

    So evidence now includes anything that only exists because somebody did the
    work of recording a real client — a contact profile, a logged conversation,
    a domain we renew for them.

    ── AND WHERE THERE IS GENUINELY NOTHING ────────────────────────────────────

    It is still allowed, but it stops being silent. `outside_system` must say
    where the work came from, and that sentence goes into the log next to the
    invoice number. A hard refusal here would only teach people to type a fake
    order to get past it, which is worse: the pipeline would fill with
    fictional work and the guard would have caused the thing it existed to
    prevent.
    """
    has_history = (
        organisation.orders.exists()
        or Invoice.objects.filter(organisation=organisation).exists()
        # Added when the guard was widened. Each of these exists only because
        # somebody recorded a real client: a phone number and a contact name,
        # a conversation, a domain we renew on their behalf.
        or organisation.contact_log.exists()
        or organisation.hosting.exists()
        or ClientProfile.objects.filter(organisation=organisation)
        .exclude(contact_name="", client_since=None, what_they_do="")
        .exists()
    )
    outside_system = (outside_system or "").strip()
    if not has_history and not outside_system:
        raise OperationsError(
            f"There is nothing on file for {organisation.name} — no orders, no "
            "invoices, no conversations, nothing we run for them. If the work "
            "predates this system, say where it came from and this will go "
            "through; the note is kept with the invoice. Otherwise check this "
            "is the right client.",
            field="outside_system",
        )

    description = description.strip()
    if not description:
        raise OperationsError(
            "Say what this bills. The client's accounts department reads this "
            "line and nothing else.",
            field="description",
        )

    if amount_kes is None:
        raise OperationsError("An invoice needs an amount.", field="amount_kes")
    amount_kes = Decimal(amount_kes)
    if amount_kes <= 0:
        raise OperationsError(
            "An invoice must ask for a positive amount. A refund or a "
            "correction is a credit note, not an invoice.",
            field="amount_kes",
        )

    issued_on = issued_on or timezone.localdate()
    if due_on and due_on < issued_on:
        raise OperationsError(
            "That due date is before the invoice date, so it would arrive "
            "already overdue.",
            field="due_on",
        )

    for attempt in range(5):
        try:
            with transaction.atomic():
                invoice = Invoice.objects.create(
                    number=next_invoice_number(issued_on),
                    organisation=organisation,
                    billed_to_name=organisation.name,
                    order=None,
                    milestone=None,
                    description=description,
                    amount_kes=amount_kes,
                    issued_on=issued_on,
                    due_on=due_on,
                    issued_by=actor,
                )
            break
        except IntegrityError:
            if attempt == 4:
                raise
    else:  # pragma: no cover - the loop always breaks or raises
        raise OperationsError("Could not allocate an invoice number.")

    notify_invoice_issued(invoice)
    record(
        actor=actor,
        action=ActivityLog.Action.INVOICE_ISSUED,
        subject=invoice.number,
        organisation=organisation,
        summary=(
            f"{invoice.number} issued to {organisation.name} for "
            f"KES {invoice.amount_kes:,.2f} (no order)"
            # Where the work came from, when there was nothing on file. Kept
            # in the summary rather than only in `detail` so it is readable in
            # the log without opening anything — this is the line somebody
            # will want months later when asking what this invoice was for.
            + (f" — {outside_system}" if outside_system else "")
        ),
        amount_kes=str(invoice.amount_kes),
        direct=True,
        outside_system=outside_system or None,
    )

    log.info(
        "direct invoice %s issued to %s by %s",
        invoice.number,
        organisation.name,
        actor.email,
    )
    return invoice


@transaction.atomic
def record_payment(
    *,
    invoice: Invoice,
    actor: User,
    amount_kes: Decimal | None = None,
    method: str = PaymentRecord.Method.MPESA,
    reference: str = "",
    paid_on: date | None = None,
    note: str = "",
    mpesa_payment: MpesaPayment | None = None,
) -> Invoice:
    """
    Write down that money arrived. It does not move any.

    ── PAYMENTS ADD UP; THEY DO NOT OVERWRITE ──────────────────────────────────

    This used to set a single reference on the invoice and mark it paid. That
    is wrong for the way invoices are actually settled here: M-Pesa caps a
    single transaction, so a large invoice arrives as several transfers with
    several codes. Under the old shape the second code replaced the first and
    the invoice claimed to be settled by a payment covering a fraction of it.

    Now each payment is its own row, and the invoice is marked paid when the
    rows ADD UP to the full amount. Below that it stays outstanding with a
    balance, which is the truth and is what the client sees.

    ── WHY OVERPAYMENT IS REFUSED ──────────────────────────────────────────────

    Recording more than is owed is almost always a typo in the amount, and the
    version of it that is not a typo — a client genuinely sending too much —
    needs a decision from a person, not a row that silently absorbs it. Either
    way the right response is to stop and say so.
    """
    if invoice.status == Invoice.Status.VOID:
        raise OperationsError(
            f"{invoice.number} was voided. A voided invoice is not owed, so a "
            "payment against it needs a new invoice first.",
        )
    if invoice.status == Invoice.Status.PAID:
        raise OperationsError(f"{invoice.number} is already recorded as paid.")

    reference = reference.strip()
    if not reference and method != PaymentRecord.Method.CASH:
        raise OperationsError(
            "Record the payment reference — the M-Pesa code or bank reference. "
            "Without it this row cannot be checked against the account.",
            field="reference",
        )

    if method not in PaymentRecord.Method.values:
        raise OperationsError("That is not a payment method we record.", field="method")

    # Default to settling the invoice outright, which is the common case and
    # keeps a one-payment invoice a one-field form.
    if amount_kes is None:
        amount_kes = invoice.balance
    amount_kes = Decimal(amount_kes)

    if amount_kes <= 0:
        raise OperationsError(
            "A payment has to be for a positive amount.", field="amount_kes"
        )

    paid_on = paid_on or timezone.localdate()
    if paid_on < invoice.issued_on:
        raise OperationsError(
            "That payment date is before the invoice was issued.",
            field="paid_on",
        )

    # Locked, because two people recording the last two payments at once must
    # not both read the same balance and both decide theirs settles it.
    #
    # The lock is taken and the result discarded, then the CALLER'S instance is
    # refreshed. Rebinding to the fetched row instead would leave every caller
    # holding an object that still says ISSUED after this function marked it
    # PAID — which is how a settled invoice gets voided a line later.
    Invoice.objects.select_for_update().get(pk=invoice.pk)
    invoice.refresh_from_db()
    outstanding = invoice.balance

    if amount_kes > outstanding:
        raise OperationsError(
            f"That is more than is outstanding on {invoice.number}. "
            f"KES {outstanding:,.2f} is owed and this records "
            f"KES {amount_kes:,.2f}. Check the amount.",
            field="amount_kes",
        )

    try:
        payment = PaymentRecord.objects.create(
            invoice=invoice,
            method=method,
            reference=reference,
            amount_kes=amount_kes,
            paid_on=paid_on,
            note=note.strip(),
            recorded_by=actor,
            mpesa_payment=mpesa_payment,
        )
    except IntegrityError:
        # The unique constraint on the reference. An M-Pesa code is unique
        # across the network, so seeing one twice means this payment is
        # already recorded somewhere — possibly against a different invoice,
        # which would show us as having been paid money we were not.
        raise OperationsError(
            f"{reference} is already recorded against an invoice. The same "
            "payment cannot settle two of them.",
            field="reference",
        ) from None

    settled = invoice.balance <= 0
    if settled:
        invoice.status = Invoice.Status.PAID
        invoice.paid_on = paid_on
        # Every reference that made it up, in the order they arrived, so the
        # invoice can be checked against a statement without a join.
        refs = [p.reference for p in invoice.payments.all() if p.reference]
        invoice.payment_reference = ", ".join(refs)[:120]
        invoice.recorded_by = actor
        invoice.save(
            update_fields=["status", "paid_on", "payment_reference", "recorded_by"]
        )

        if invoice.milestone_id:
            invoice.milestone.mark_paid()

    notify_payment_recorded(invoice, payment, settled=settled)
    record(
        actor=actor,
        action=(
            ActivityLog.Action.INVOICE_PAID
            if settled
            else ActivityLog.Action.PAYMENT_RECORDED
        ),
        subject=invoice.number,
        organisation=invoice.organisation,
        summary=(
            f"KES {amount_kes:,.2f} recorded against {invoice.number}"
            + (" — settled in full" if settled else f", KES {invoice.balance:,.2f} outstanding")
        ),
        method=method,
        # The payment reference, not a secret: it is on the client's statement
        # and on the invoice already.
        reference=reference,
        amount_kes=str(amount_kes),
    )

    log.info(
        "payment of %s recorded on %s by %s (%s)",
        amount_kes,
        invoice.number,
        actor.email if actor else "system",
        "settled" if settled else f"balance {invoice.balance}",
    )
    return invoice



@transaction.atomic
def void_invoice(*, invoice: Invoice, actor: User, reason: str) -> Invoice:
    """
    Withdraw an invoice that should not have been sent.

    A PAID invoice cannot be voided. Voiding it would erase the record of money
    that actually arrived, leaving the client's statement showing a payment
    against a document we say never existed. If the money needs to go back,
    that is a refund and a credit note — a real transaction, not the deletion
    of a row.

    The invoice keeps its number. Gaps in a sequence are explainable; a reused
    number is not.
    """
    if invoice.status == Invoice.Status.PAID:
        raise OperationsError(
            f"{invoice.number} has been paid. Voiding it would erase the record "
            "of money that arrived — a refund is a credit note, not a void.",
        )
    if invoice.status == Invoice.Status.VOID:
        raise OperationsError(f"{invoice.number} is already void.")

    reason = reason.strip()
    if not reason:
        raise OperationsError(
            "Say why this invoice is being withdrawn.", field="reason"
        )

    invoice.status = Invoice.Status.VOID
    invoice.void_reason = reason
    invoice.voided_at = timezone.now()
    invoice.save(update_fields=["status", "void_reason", "voided_at"])

    # The milestone becomes billable again — that is the whole point of a void.
    if invoice.milestone_id and invoice.milestone.status == Milestone.Status.INVOICED:
        invoice.milestone.status = Milestone.Status.PENDING
        invoice.milestone.save(update_fields=["status"])

    notify_invoice_voided(invoice)
    record(
        actor=actor,
        action=ActivityLog.Action.INVOICE_VOIDED,
        subject=invoice.number,
        organisation=invoice.organisation,
        summary=f"{invoice.number} withdrawn: {reason}",
        amount_kes=str(invoice.amount_kes),
    )

    log.info("invoice %s voided by %s: %s", invoice.number, actor.email, reason)
    return invoice


# ── M-Pesa ───────────────────────────────────────────────────────────────────


def start_mpesa_payment(*, invoice: Invoice, phone: str) -> MpesaPayment:
    """
    Prompt a phone to pay this invoice.

    ── WHAT THIS DOES NOT DO ──────────────────────────────────────────────────

    It does not mark anything paid. A successful STK response means Safaricom
    accepted the prompt for delivery, nothing more — the customer has not seen
    it yet, let alone entered a PIN. Only the callback decides that.

    ── WHOLE SHILLINGS ONLY ───────────────────────────────────────────────────

    Daraja's Amount is an integer; M-Pesa cannot move cents. An invoice for
    133,333.33 therefore cannot be paid exactly, and the two available fudges
    are both wrong: rounding down under-collects, rounding up takes money the
    client never agreed to. So it is refused, with the reason and a working
    alternative, rather than silently producing a payment that reconciles
    against nothing.
    """
    if not settings.MPESA_ENABLED:
        raise OperationsError("M-Pesa is not set up on this server yet.")
    if invoice.status == Invoice.Status.PAID:
        raise OperationsError(f"{invoice.number} is already paid.")
    if invoice.status == Invoice.Status.VOID:
        raise OperationsError(f"{invoice.number} was withdrawn, so nothing is owed.")

    if invoice.amount_kes != invoice.amount_kes.to_integral_value():
        raise OperationsError(
            f"{invoice.number} is for KES {invoice.amount_kes}, and M-Pesa can "
            "only take whole shillings. Use the paybill details on the invoice, "
            "or ask us to reissue it rounded.",
            field="amount",
        )

    try:
        msisdn = mpesa.normalise_phone(phone)
    except mpesa.MpesaError as exc:
        raise OperationsError(str(exc), field="phone") from exc

    amount = int(invoice.amount_kes)

    # An unpaid push to the same number for the same invoice is almost always
    # somebody pressing the button again because the first prompt has not
    # arrived. Sending a second prompt is right; creating a row per impatient
    # tap is not, so the previous pending attempt is closed out first.
    invoice.mpesa_payments.filter(
        status=MpesaPayment.Status.PENDING, phone=msisdn
    ).update(
        status=MpesaPayment.Status.FAILED,
        result_desc="Superseded by a later prompt to the same number.",
        completed_at=timezone.now(),
    )

    response = mpesa.stk_push(
        phone=msisdn,
        amount=amount,
        reference=invoice.number,
        description="Invoice",
    )

    payment = MpesaPayment.objects.create(
        invoice=invoice,
        checkout_request_id=response.get("CheckoutRequestID", ""),
        merchant_request_id=response.get("MerchantRequestID", ""),
        phone=msisdn,
        amount=amount,
    )
    log.info(
        "mpesa prompt sent for %s (%s) live=%s",
        invoice.number,
        payment.checkout_request_id,
        settings.MPESA_IS_LIVE,
    )
    return payment


@transaction.atomic
def record_mpesa_result(parsed: dict) -> MpesaPayment | None:
    """
    Apply a Daraja callback. THE ONLY PLACE M-PESA MARKS AN INVOICE PAID.

    ── EVERY GUARD HERE EXISTS BECAUSE THE CALLER IS UNTRUSTED ────────────────

    Daraja sends no signature and no credential. The URL carries a shared
    token, but that is one secret in a path — it is not authentication, and it
    can end up in a proxy log. So this function assumes the body might be
    forged and refuses to do anything that would matter if it were:

      · The CheckoutRequestID must match a payment WE started. An attacker
        cannot invent one, because we only ever created it from Safaricom's own
        response to a push we made.

      · A payment already resolved is left alone. Safaricom retries callbacks,
        and a retry must not pay an invoice twice or overwrite a receipt.
        select_for_update, because two retries can land in parallel.

      · THE AMOUNT MUST MATCH WHAT WE ASKED FOR. This is the guard that matters
        most. Without it, a forged callback claiming one shilling settles a
        two-hundred-thousand-shilling invoice. A mismatch is recorded in full
        and the invoice is NOT marked paid — a human looks at it, which is the
        right outcome for money that does not add up.
    """
    checkout_id = parsed.get("checkout_request_id")
    if not checkout_id:
        log.warning("mpesa callback with no CheckoutRequestID")
        return None

    payment = (
        MpesaPayment.objects.select_for_update()
        .filter(checkout_request_id=checkout_id)
        .select_related("invoice")
        .first()
    )
    if payment is None:
        # Not ours. Either a stray retry from a previous deployment, or
        # somebody probing the endpoint.
        log.warning("mpesa callback for unknown CheckoutRequestID %s", checkout_id)
        return None

    if payment.status != MpesaPayment.Status.PENDING:
        log.info("mpesa callback for already-resolved %s, ignored", checkout_id)
        return payment

    payment.raw_callback = parsed.get("raw")
    payment.result_code = parsed["result_code"][:8]
    payment.result_desc = parsed["result_desc"]
    payment.completed_at = timezone.now()

    # Anything but "0" is a cancellation, a timeout, insufficient funds or a
    # wrong PIN. All ordinary, none of them a payment.
    if parsed["result_code"] != "0":
        payment.status = MpesaPayment.Status.FAILED
        payment.save()
        log.info("mpesa %s failed: %s", checkout_id, payment.result_desc[:120])
        return payment

    paid = parsed.get("amount")
    try:
        paid_int = int(Decimal(str(paid)))
    except (TypeError, ValueError, ArithmeticError):
        paid_int = None

    if paid_int != payment.amount:
        # Recorded, flagged, and NOT applied. Either something is wrong at
        # Safaricom or the callback is forged; both need a person.
        payment.status = MpesaPayment.Status.FAILED
        payment.receipt = str(parsed.get("receipt") or "")[:32]
        payment.result_desc = (
            f"Amount mismatch: asked for {payment.amount}, callback said {paid}. "
            "Not applied to the invoice."
        )
        payment.save()
        log.error(
            "mpesa AMOUNT MISMATCH on %s: asked %s, got %s",
            payment.invoice.number,
            payment.amount,
            paid,
        )
        return payment

    payment.status = MpesaPayment.Status.SUCCESS
    payment.receipt = str(parsed.get("receipt") or "")[:32]
    payment.save()

    invoice = payment.invoice
    # Already settled some other way — someone recorded a bank transfer while
    # the prompt was open. The payment stands as a record; the invoice is not
    # double-marked, and the mismatch is loud because it means money may have
    # arrived twice.
    if invoice.status == Invoice.Status.PAID:
        log.error(
            "mpesa %s succeeded for %s which was ALREADY PAID — possible double payment",
            payment.receipt,
            invoice.number,
        )
        return payment

    # Through the same door as every other payment, so there is ONE ledger.
    #
    # This used to mark the invoice paid directly. With PaymentRecord that
    # would have been a visible lie: the invoice would say PAID while
    # amount_paid stayed at zero and the client's dashboard showed the full
    # amount still outstanding.
    #
    # record_payment applies the same balance arithmetic as a bank transfer,
    # so a part-payment by M-Pesa now leaves a correct balance instead of
    # settling the whole invoice.
    try:
        record_payment(
            invoice=invoice,
            actor=None,
            amount_kes=Decimal(payment.amount),
            method=PaymentRecord.Method.MPESA,
            reference=payment.receipt,
            paid_on=timezone.localdate(),
            note="Recorded automatically from the M-Pesa callback.",
            mpesa_payment=payment,
        )
    except OperationsError as exc:
        # The money arrived; only the bookkeeping refused. Loud, because a
        # successful payment that did not reach an invoice needs a person
        # today, not at month end.
        log.error(
            "mpesa %s succeeded for %s but could not be recorded: %s",
            payment.receipt,
            invoice.number,
            exc,
        )
        return payment

    log.info("mpesa %s paid %s", payment.receipt, invoice.number)
    return payment


# ── incidents ────────────────────────────────────────────────────────────────


def next_incident_reference(today: date | None = None) -> str:
    year = (today or timezone.localdate()).year
    prefix = f"{REFERENCE_PREFIX}-INC-{year}-"
    used = Incident.objects.filter(reference__startswith=prefix).count()
    return f"{prefix}{used + 1:04d}"


@transaction.atomic
def raise_incident(
    *,
    actor: User,
    title: str,
    severity: str,
    started_at,
    detected_at=None,
    summary: str = "",
    client_impact: str = "",
) -> Incident:
    """
    Record that something broke.

    ── WHY started_at IS REQUIRED AND SEPARATE FROM detected_at ────────────────

    They are almost never the same moment, and the gap between them is the most
    useful number in the record: it is how long the failure ran with nobody
    looking. Collapsing them into one field loses exactly the measurement that
    tells you whether monitoring works — and a system with no such measurement
    will keep believing it does.

    Detection defaults to now, because the usual case is writing this up on
    finding out. Start time never defaults; it has to be thought about.
    """
    title = title.strip()
    if not title:
        raise OperationsError("Say what broke.", field="title")

    if severity not in Incident.Severity.values:
        raise OperationsError("That is not a severity we use.", field="severity")

    if started_at is None:
        raise OperationsError(
            "When did it start? Not when you noticed — the gap between the two "
            "is how long it ran unseen, and it is the point of recording it.",
            field="started_at",
        )

    detected_at = detected_at or timezone.now()
    if detected_at < started_at:
        raise OperationsError(
            "It cannot have been detected before it began.", field="detected_at"
        )

    summary = summary.strip()
    if not summary:
        raise OperationsError(
            "Describe what was happening. A title alone is not a record.",
            field="summary",
        )

    for attempt in range(5):
        try:
            with transaction.atomic():
                incident = Incident.objects.create(
                    reference=next_incident_reference(),
                    title=title,
                    severity=severity,
                    started_at=started_at,
                    detected_at=detected_at,
                    summary=summary,
                    client_impact=client_impact.strip(),
                    raised_by=actor,
                )
            break
        except IntegrityError:
            if attempt == 4:
                raise
    else:  # pragma: no cover - the loop always breaks or raises
        raise OperationsError("Could not allocate an incident reference.")

    _notify(
        users=_staff_recipients(),
        audience=Notification.Audience.STAFF,
        kind=Notification.Kind.INCIDENT_RAISED,
        title=f"{incident.get_severity_display().split(' — ')[0]}: {incident.title}",
        body=incident.summary[:200],
        url="/incidents",
    )

    record(
        actor=actor,
        action=ActivityLog.Action.INCIDENT_RAISED,
        subject=incident.reference,
        summary=f"{incident.get_severity_display().split(' — ')[0]} raised: {incident.title}",
        severity=severity,
    )

    log.info(
        "incident %s raised (%s) by %s", incident.reference, severity, actor.email
    )
    return incident


@transaction.atomic
def write_post_mortem(
    *,
    incident: Incident,
    actor: User,
    what_happened: str | None = None,
    why: str | None = None,
    prevention: str | None = None,
) -> Incident:
    """
    Fill in any of the three parts. They can be written as they become known.

    Deliberately partial: a post-mortem written in one sitting on the day of the
    incident is a guess about the cause. Letting the parts land separately is
    what makes it likely they are true.
    """
    fields = []
    for name, value in (
        ("what_happened", what_happened),
        ("why", why),
        ("prevention", prevention),
    ):
        if value is not None:
            setattr(incident, name, value.strip())
            fields.append(name)

    if not fields:
        return incident

    incident.save(update_fields=fields)
    log.info("post-mortem updated on %s by %s", incident.reference, actor.email)
    return incident


@transaction.atomic
def close_incident(*, incident: Incident, actor: User, resolved_at=None) -> Incident:
    """
    Close it.

    ══════════════════════════════════════════════════════════════════════════
    THE GUARD THAT MAKES A PUBLISHED PROMISE TRUE.

    genmars.co.ke/approach tells the public that every SEV-1 produces a written
    post-mortem. A promise kept by memory is kept until the first busy week, and
    the busy week is exactly when the SEV-1 happens.

    So a SEV-1 cannot be closed until all three parts are written. This will be
    resented at 2am by whoever wants the board clear, and that is the moment it
    is doing its job: the incident is still closable the instant somebody writes
    down what prevents it recurring, which is the only part that changes
    anything.

    SEV-2 and SEV-3 close freely. The website does not promise a post-mortem for
    those, and a rule that applies to everything gets worked around.
    ══════════════════════════════════════════════════════════════════════════
    """
    if incident.status == Incident.Status.CLOSED:
        raise OperationsError(f"{incident.reference} is already closed.")

    if incident.needs_post_mortem:
        missing = [
            label
            for label, value in (
                ("what happened", incident.what_happened),
                ("why", incident.why),
                ("what prevents recurrence", incident.prevention),
            )
            if not value.strip()
        ]
        raise OperationsError(
            f"{incident.reference} is a SEV-1 and its post-mortem is missing "
            + ", ".join(missing)
            + ". genmars.co.ke/approach promises every SEV-1 gets one, so this "
            "cannot close without it.",
        )

    incident.status = Incident.Status.CLOSED
    incident.resolved_at = resolved_at or timezone.now()
    incident.closed_by = actor
    incident.save(update_fields=["status", "resolved_at", "closed_by"])

    record(
        actor=actor,
        action=ActivityLog.Action.INCIDENT_CLOSED,
        subject=incident.reference,
        summary=f"{incident.reference} closed: {incident.title}",
        severity=incident.severity,
    )

    log.info("incident %s closed by %s", incident.reference, actor.email)
    return incident


@transaction.atomic
def mitigate_incident(*, incident: Incident, actor: User, mitigated_at=None) -> Incident:
    """
    The bleeding has stopped; the cause has not been fixed.

    A real state and a common one. Collapsing it into "closed" is how a
    workaround quietly becomes the permanent solution.
    """
    if incident.status != Incident.Status.OPEN:
        raise OperationsError(
            f"{incident.reference} is {incident.get_status_display().lower()}."
        )

    incident.status = Incident.Status.MITIGATED
    incident.mitigated_at = mitigated_at or timezone.now()
    incident.save(update_fields=["status", "mitigated_at"])
    log.info("incident %s mitigated by %s", incident.reference, actor.email)
    return incident


# ── catalogue pricing ────────────────────────────────────────────────────────


@transaction.atomic
def set_tier_price(
    *,
    tier: ServiceTier,
    actor: User,
    price_kes: Decimal | None,
    is_from: bool | None = None,
) -> ServiceTier:
    """
    Change what a tier costs.

    ── WHY THIS IS A SERVICE AND NOT A SERIALIZER SAVE ─────────────────────────

    A price is the most consequential editable field in the system. It is
    quoted on a public page, it pre-fills the budget on an order, and it ends
    up copied into an invoice that somebody pays. So the change is logged with
    the old and new values, permanently, by whoever made it — which is the only
    way to answer "when did this become 75,000" six months later.

    The published price is deliberately NOT touched. genmars.co.ke is a static
    export on its own deploy cycle; this changes the portal now and the website
    when someone ships it. ServiceTier.differs_from_website is how that gap is
    surfaced rather than hidden.
    """
    if price_kes is not None:
        price_kes = Decimal(price_kes)
        if price_kes <= 0:
            raise OperationsError(
                "A tier has to cost something. A free tier is a different "
                "conversation from a zero-priced one.",
                field="price_kes",
            )

    before = tier.price_kes
    changed = []

    if price_kes != before:
        tier.price_kes = price_kes
        changed.append("price_kes")

    if is_from is not None and is_from != tier.is_from:
        tier.is_from = is_from
        changed.append("is_from")

    if not changed:
        return tier

    tier.save(update_fields=changed)

    record(
        actor=actor,
        action=ActivityLog.Action.PRICE_CHANGED,
        subject=f"{tier.service.slug}/{tier.slug}",
        summary=(
            f"{tier.service.name} — {tier.name}: "
            f"KES {before:,.2f} to KES {tier.price_kes:,.2f}"
            if before is not None and tier.price_kes is not None
            else f"{tier.service.name} — {tier.name} price updated"
        ),
        was=str(before) if before is not None else None,
        now=str(tier.price_kes) if tier.price_kes is not None else None,
        website_says=(
            str(tier.published_price_kes)
            if tier.published_price_kes is not None
            else None
        ),
    )

    log.info(
        "tier %s/%s priced %s -> %s by %s",
        tier.service.slug, tier.slug, before, tier.price_kes, actor.email,
    )
    return tier


# ── offers ───────────────────────────────────────────────────────────────────


def next_offer_reference(today: date | None = None) -> str:
    year = (today or timezone.localdate()).year
    prefix = f"{REFERENCE_PREFIX}-OFR-{year}-"
    used = Offer.objects.filter(reference__startswith=prefix).count()
    return f"{prefix}{used + 1:04d}"


PROPOSAL_FIELDS = (
    "context",
    "approach",
    "inclusions",
    "exclusions",
    "timeline",
    "payment_terms",
    "next_step",
)


@transaction.atomic
def revise_offer(*, offer: Offer, actor: User, values: dict) -> Offer:
    """
    Edit a DRAFT.

    Refuses anything else, and the reason is the whole point of the two-step
    send: once an offer is sent the client can accept it, so the number and the
    words are ours to honour. Editing them under somebody who is still deciding
    means they open on Friday something different from what they read on
    Tuesday, and neither of us can explain what happened.

    A sent offer that was wrong is WITHDRAWN and replaced, which leaves both
    versions readable.
    """
    if offer.status != Offer.Status.DRAFT:
        raise OperationsError(
            f"{offer.reference} is {offer.get_status_display().lower()}. A sent "
            "offer is not edited — withdraw it and make a new one, so both "
            "versions stay readable and the client can see what changed."
        )

    editable = ("title", "detail", "amount_kes", "expires_on", *PROPOSAL_FIELDS)
    changed = []
    for field in editable:
        if field not in values:
            continue
        new = values[field]
        if isinstance(new, str):
            new = new.strip()
        if new != getattr(offer, field):
            setattr(offer, field, new)
            changed.append(field)

    if not (offer.title or "").strip():
        raise OperationsError("Say what is being offered.", field="title")
    if not (offer.detail or "").strip() and not (offer.inclusions or "").strip():
        raise OperationsError("Say what it includes.", field="detail")
    if offer.amount_kes is not None and Decimal(offer.amount_kes) <= 0:
        raise OperationsError(
            "An offer has to be for a positive amount.", field="amount_kes"
        )
    if offer.expires_on and offer.expires_on < timezone.localdate():
        raise OperationsError("That date has already passed.", field="expires_on")

    if changed:
        offer.save(update_fields=changed)
    return offer


@transaction.atomic
def make_offer(
    *,
    organisation: Organisation,
    actor: User,
    title: str,
    detail: str,
    amount_kes: Decimal,
    expires_on: date,
    service: Service | None = None,
    tier: ServiceTier | None = None,
    **proposal,
) -> Offer:
    """
    Put a price to a client. Created as a DRAFT — sending is a separate act.

    ── WHY DRAFT AND SEND ARE TWO STEPS ────────────────────────────────────────

    Once sent, the client can accept it and the number becomes ours to honour.
    That is not something to do with one click on a form that might have a typo
    in the amount. Drafting is cheap and editable; sending is a commitment and
    freezes it.

    ── WHY THE LIST PRICE IS STORED ────────────────────────────────────────────

    So a discount is a visible decision rather than a number nobody can check.
    "We offered 60,000" says nothing on its own; "we offered 60,000 against a
    list price of 75,000" is a fact somebody can review.
    """
    title = title.strip()
    if not title:
        raise OperationsError("Say what is being offered.", field="title")

    detail = (detail or "").strip()
    # Either the summary line or the structured "what the price covers" has to
    # say something. A price with no description of what it buys is a number
    # the client cannot evaluate and we cannot later prove we described.
    if not detail and not (proposal.get("inclusions") or "").strip():
        raise OperationsError(
            "Say what it includes. The client reads this and decides on it.",
            field="detail",
        )

    amount_kes = Decimal(amount_kes)
    if amount_kes <= 0:
        raise OperationsError(
            "An offer has to be for a positive amount.", field="amount_kes"
        )

    if expires_on is None:
        raise OperationsError(
            "Say when it expires. An open-ended price is one we are still bound "
            "by in a year, after costs have moved.",
            field="expires_on",
        )
    if expires_on < timezone.localdate():
        raise OperationsError(
            "That date has already passed.", field="expires_on"
        )

    for attempt in range(5):
        try:
            with transaction.atomic():
                offer = Offer.objects.create(
                    reference=next_offer_reference(),
                    organisation=organisation,
                    service=service or (tier.service if tier else None),
                    tier_name=tier.name if tier else "",
                    title=title,
                    detail=detail,
                    amount_kes=amount_kes,
                    list_price_kes=tier.price_kes if tier else None,
                    expires_on=expires_on,
                    created_by=actor,
                    **{
                        field: (proposal.get(field) or "").strip()
                        for field in PROPOSAL_FIELDS
                    },
                )
            break
        except IntegrityError:
            if attempt == 4:
                raise
    else:  # pragma: no cover
        raise OperationsError("Could not allocate an offer reference.")

    log.info("offer %s drafted for %s by %s", offer.reference, organisation.name, actor.email)
    return offer


@transaction.atomic
def send_offer(*, offer: Offer, actor: User) -> Offer:
    """
    Put it in front of the client. From here the amount is frozen.

    See Offer's docstring: an offer that recalculated from the catalogue would
    change under a client who was still deciding, and neither of us could
    explain what happened.
    """
    if offer.status != Offer.Status.DRAFT:
        raise OperationsError(
            f"{offer.reference} is {offer.get_status_display().lower()} and cannot be sent again."
        )
    if offer.is_expired():
        raise OperationsError(
            "That offer expires before it would be sent. Move the date first.",
            field="expires_on",
        )

    offer.status = Offer.Status.SENT
    offer.sent_at = timezone.now()
    # Frozen at send, like the amount and for the same reason: this is the
    # moment the document becomes something the client holds a copy of, and
    # renaming the organisation later must not change who it was addressed to.
    offer.offered_to_name = offer.organisation.name
    offer.save(update_fields=["status", "sent_at", "offered_to_name"])

    _notify(
        users=_client_recipients(offer.organisation),
        audience=Notification.Audience.CLIENT,
        kind=Notification.Kind.OFFER_SENT,
        title=f"An offer from Genmars: {offer.title}",
        body=f"KES {offer.amount_kes:,.2f}, valid until {offer.expires_on:%-d %B %Y}.",
        url="/offers",
    )
    record(
        actor=actor,
        action=ActivityLog.Action.OFFER_SENT,
        subject=offer.reference,
        organisation=offer.organisation,
        summary=(
            f"{offer.reference} sent to {offer.organisation.name}: "
            f"{offer.title}, KES {offer.amount_kes:,.2f}"
        ),
        amount_kes=str(offer.amount_kes),
        list_price_kes=str(offer.list_price_kes) if offer.list_price_kes else None,
        expires_on=str(offer.expires_on),
    )

    # The email, which is the half that actually reaches anybody. A price
    # sitting behind a login is indistinguishable, from our side, from having
    # quoted somebody who went quiet.
    _email_offer(offer)

    log.info("offer %s sent by %s", offer.reference, actor.email)
    return offer


def _email_offer(offer: Offer) -> None:
    """
    Send the quote to everyone at the client who takes updates.

    Same two exclusions as a progress note. `receives_updates` off means they
    asked not to hear from us, and an unverified address is one nobody has
    proved they read — a price is commercial detail and does not go to a
    mailbox we cannot place.
    """
    proposal = {
        "context": offer.context,
        "approach": offer.approach,
        "inclusions": offer.inclusions,
        "exclusions": offer.exclusions,
        "timeline": offer.timeline,
    }
    # Fall back to the single `detail` blob when none of the structured fields
    # were used, so a quote written the old way still says something.
    if not any(v.strip() for v in proposal.values()):
        proposal = {"inclusions": offer.detail}

    recipients = (
        Membership.objects.filter(
            organisation=offer.organisation, receives_updates=True
        )
        .select_related("user")
        .exclude(user__email_verified_at__isnull=True)
    )

    for membership in recipients:
        try:
            emails.send_offer(
                email=membership.user.email,
                reference=offer.reference,
                title=offer.title,
                amount_kes=f"{offer.amount_kes:,.2f}",
                list_price_kes=(
                    f"{offer.list_price_kes:,.2f}"
                    if offer.list_price_kes and offer.list_price_kes != offer.amount_kes
                    else ""
                ),
                expires_on=f"{offer.expires_on:%-d %B %Y}",
                proposal={k: v for k, v in proposal.items() if v.strip()},
                payment_terms=offer.payment_terms,
                next_step=offer.next_step,
            )
        except Exception:
            # A failed email must not roll back the send. The offer is the
            # fact and it is already in their dashboard; this is an account of
            # it, and one that failed is logged rather than swallowed silently.
            log.exception(
                "could not email %s about offer %s", membership.user.email, offer.reference
            )


@transaction.atomic
def accept_offer(*, offer: Offer, actor: User) -> Offer:
    """
    The client says yes.

    ── ACCEPTING DOES NOT START WORK ───────────────────────────────────────────

    It files an enquiry carrying the offer, which the commercial partners
    qualify like any other. Charter 02 §I puts a signed statement of work
    before delivery, and no click by a client may skip that.
    """
    if offer.status != Offer.Status.SENT:
        raise OperationsError(
            f"{offer.reference} is {offer.get_status_display().lower()}."
        )
    if offer.is_expired():
        raise OperationsError(
            f"{offer.reference} expired on {offer.expires_on:%-d %B %Y}. "
            "Ask us for a fresh one — we would rather requote than hold you to "
            "a price we set months ago.",
        )

    enquiry = Enquiry.objects.create(
        organisation=offer.organisation,
        submitted_by=actor,
        problem=f"Accepted offer {offer.reference}: {offer.title}",
        service=offer.service,
        tier=offer.tier_name,
        budget_range=f"KES {offer.amount_kes:,.2f} (offered)",
    )

    offer.status = Offer.Status.ACCEPTED
    offer.decided_at = timezone.now()
    offer.accepted_by = actor
    offer.enquiry = enquiry
    offer.save(update_fields=["status", "decided_at", "accepted_by", "enquiry"])

    notify_enquiry_received(enquiry)
    record(
        actor=actor,
        action=ActivityLog.Action.OFFER_ACCEPTED,
        subject=offer.reference,
        organisation=offer.organisation,
        summary=f"{offer.organisation.name} accepted {offer.reference} at KES {offer.amount_kes:,.2f}",
        amount_kes=str(offer.amount_kes),
    )

    log.info("offer %s accepted by %s", offer.reference, actor.email)
    return offer


@transaction.atomic
def decline_offer(*, offer: Offer, actor: User, reason: str = "") -> Offer:
    """The client says no. The reason is optional and worth asking for."""
    if offer.status != Offer.Status.SENT:
        raise OperationsError(
            f"{offer.reference} is {offer.get_status_display().lower()}."
        )

    offer.status = Offer.Status.DECLINED
    offer.decided_at = timezone.now()
    offer.decline_reason = reason.strip()
    offer.save(update_fields=["status", "decided_at", "decline_reason"])
    log.info("offer %s declined", offer.reference)
    return offer


@transaction.atomic
def withdraw_offer(*, offer: Offer, actor: User, reason: str) -> Offer:
    """
    Take it back before it is accepted.

    An accepted offer cannot be withdrawn — the client acted on it, and undoing
    that unilaterally is not something this system should make easy.
    """
    if offer.status == Offer.Status.ACCEPTED:
        raise OperationsError(
            f"{offer.reference} has been accepted. That is a conversation with "
            "the client, not a status change.",
        )
    if not offer.is_open:
        raise OperationsError(
            f"{offer.reference} is {offer.get_status_display().lower()}."
        )

    reason = reason.strip()
    if not reason:
        raise OperationsError("Say why it is being withdrawn.", field="reason")

    offer.status = Offer.Status.WITHDRAWN
    offer.decided_at = timezone.now()
    offer.decline_reason = reason
    offer.save(update_fields=["status", "decided_at", "decline_reason"])

    record(
        actor=actor,
        action=ActivityLog.Action.OFFER_WITHDRAWN,
        subject=offer.reference,
        organisation=offer.organisation,
        summary=f"{offer.reference} withdrawn: {reason}",
    )
    return offer


# ── tasks ────────────────────────────────────────────────────────────────────


@transaction.atomic
def assign_task(
    *,
    actor: User,
    assignee: User,
    title: str,
    detail: str = "",
    order: Order | None = None,
    organisation: Organisation | None = None,
    ticket: SupportTicket | None = None,
    decision: Decision | None = None,
    contact: ContactLogEntry | None = None,
    due_on: date | None = None,
    priority: str = Task.Priority.NORMAL,
) -> Task:
    """
    Give somebody a piece of work.

    ── ONE ASSIGNEE, ALWAYS ────────────────────────────────────────────────────

    Work assigned to everyone is assigned to nobody. The model enforces it by
    having a single non-null FK rather than a many-to-many, which is a decision
    rather than an omission.

    ── THEY HAVE TO BE ABLE TO DO IT ───────────────────────────────────────────

    Assigning to a revoked account produces a task that will never move and an
    owner who will never see it. Refused, with the reason.
    """
    title = title.strip()
    if not title:
        raise OperationsError("Say what needs doing.", field="title")

    if not assignee.is_staff:
        raise OperationsError(
            "Tasks are internal work. Only Genmars staff can be assigned one.",
            field="assignee",
        )
    if not assignee.is_active:
        raise OperationsError(
            f"{assignee.full_name or assignee.email} no longer has access, so "
            "they would never see this.",
            field="assignee",
        )

    if priority not in Task.Priority.values:
        raise OperationsError("That is not a priority we use.", field="priority")

    # The client is inferred from whatever the task is attached to, so that
    # "what is outstanding for this client" finds work filed against their
    # order or their ticket without anybody having had to set it twice.
    if organisation is None:
        if order is not None:
            organisation = order.organisation
        elif ticket is not None:
            organisation = ticket.organisation
        elif contact is not None:
            organisation = contact.organisation

    # A conversation about a specific order carries that order with it, so
    # somebody assigning work off a call does not have to retype a reference
    # that is already recorded against it.
    if order is None and contact is not None and contact.order_id is not None:
        order = contact.order

    # And the pieces must agree. A task pointing at one client's order and
    # another client's ticket is a row that appears under both and is right
    # about neither.
    for label, related in (("order", order), ("ticket", ticket), ("contact", contact)):
        if (
            related is not None
            and organisation is not None
            and related.organisation_id != organisation.id
        ):
            raise OperationsError(
                f"That {label} belongs to a different client.", field=label
            )

    task = Task.objects.create(
        title=title,
        detail=detail.strip(),
        assignee=assignee,
        assigned_by=actor,
        order=order,
        organisation=organisation,
        ticket=ticket,
        decision=decision,
        contact=contact,
        due_on=due_on,
        priority=priority,
    )

    # Only the person it lands on. A team-wide notification for one person's
    # task is how people learn to ignore notifications.
    _notify(
        users=[assignee],
        audience=Notification.Audience.STAFF,
        kind=Notification.Kind.TASK_ASSIGNED,
        title=f"Assigned to you: {task.title}",
        body=(f"Due {task.due_on:%-d %B}" if task.due_on else "No due date"),
        url="/team",
    )
    record(
        actor=actor,
        action=ActivityLog.Action.TASK_ASSIGNED,
        subject=(
            order.reference
            if order
            else ticket.reference
            if ticket
            else decision.reference
            if decision
            else ""
        ),
        organisation=organisation,
        summary=f"{task.title} assigned to {assignee.full_name or assignee.email}",
        assignee=assignee.email,
        due_on=str(due_on) if due_on else None,
    )

    log.info("task %s assigned to %s by %s", task.pk, assignee.email, actor.email)
    return task


@transaction.atomic
def set_task_status(
    *, task: Task, actor: User, status: str, blocked_reason: str = ""
) -> Task:
    """
    Move it along.

    ══════════════════════════════════════════════════════════════════════════
    YOUR BOARD, YOUR STATUS.

    This used to be open to every staff account, which meant anybody could mark
    anybody else's work done. That is worse than being able to assign it: a
    task marked done disappears from the board, and the person actually
    responsible for it is not told — so the work stops being tracked while
    still being theirs, and the first anybody knows is a client asking why it
    never happened.

    It is also the one field where somebody else genuinely cannot know the
    answer. Whether a piece of work is finished is a fact only the person doing
    it holds.

    A FOUNDER CAN MOVE ANYTHING, because somebody has to be able to close a
    task belonging to a person who has left, or who is on a plane. That is an
    override rather than the normal path, and the log records who pressed it.
    ══════════════════════════════════════════════════════════════════════════

    Blocked needs a reason — a blocked task without one is stalled.
    """
    if task.assignee_id != actor.id and not actor.can_manage_access:
        raise OperationsError(
            f"This is {task.assignee.full_name or task.assignee.email}'s to move. "
            "Marking somebody else's work done takes it off the board without "
            "telling them, and they are the only person who knows whether it is "
            "finished."
        )

    if status not in Task.Status.values:
        raise OperationsError("That is not a status we use.", field="status")

    if status == Task.Status.BLOCKED and not blocked_reason.strip():
        raise OperationsError(
            "Blocked on what? Without that this is just a task nobody is doing.",
            field="blocked_reason",
        )

    task.status = status
    task.blocked_reason = blocked_reason.strip() if status == Task.Status.BLOCKED else ""
    task.done_at = timezone.now() if status == Task.Status.DONE else None
    task.save(update_fields=["status", "blocked_reason", "done_at"])

    if status == Task.Status.DONE:
        who = actor.full_name or actor.email
        # Names the owner too when somebody else closed it. "Completed by the
        # founder" against work that was Asha's is a materially different fact
        # from Asha completing it, and the log is where that gets untangled.
        on_behalf = (
            ""
            if task.assignee_id == actor.id
            else f" (on {task.assignee.full_name or task.assignee.email}'s board)"
        )
        record(
            actor=actor,
            action=ActivityLog.Action.TASK_DONE,
            subject=task.order.reference if task.order_id else "",
            organisation=task.organisation,
            summary=f"{task.title} completed by {who}{on_behalf}",
        )

    return task


# ── support ──────────────────────────────────────────────────────────────────


def next_ticket_reference(today: date | None = None) -> str:
    year = (today or timezone.localdate()).year
    prefix = f"{REFERENCE_PREFIX}-SUP-{year}-"
    used = SupportTicket.objects.filter(reference__startswith=prefix).count()
    return f"{prefix}{used + 1:04d}"


@transaction.atomic
def raise_ticket(
    *,
    organisation: Organisation,
    actor: User,
    subject: str,
    body: str,
    order: Order | None = None,
) -> SupportTicket:
    """
    A client asks for help.

    ── PRIORITY IS NOT A PARAMETER ─────────────────────────────────────────────

    Deliberately absent. Every client-settable priority field ends up with
    everything marked urgent, which is the same as nothing being urgent. The
    client says what is happening; someone here reads it and decides.
    """
    subject = subject.strip()
    if not subject:
        raise OperationsError("What is this about?", field="subject")

    body = body.strip()
    if not body:
        raise OperationsError(
            "Tell us what is happening. A subject line on its own is not "
            "something anybody can act on.",
            field="body",
        )

    for attempt in range(5):
        try:
            with transaction.atomic():
                ticket = SupportTicket.objects.create(
                    reference=next_ticket_reference(),
                    organisation=organisation,
                    raised_by=actor,
                    order=order,
                    subject=subject,
                )
            break
        except IntegrityError:
            if attempt == 4:
                raise
    else:  # pragma: no cover
        raise OperationsError("Could not allocate a ticket reference.")

    SupportMessage.objects.create(
        ticket=ticket,
        author=actor,
        author_label=actor.full_name or actor.email,
        from_staff=False,
        body=body,
    )

    _notify(
        users=_staff_recipients(),
        audience=Notification.Audience.STAFF,
        kind=Notification.Kind.SUPPORT_RAISED,
        title=f"{organisation.name}: {subject}",
        body=body[:200],
        url="/support",
    )

    # Mail is a convenience on top of the record, never the record itself, so a
    # provider having a bad afternoon must not lose the ticket.
    try:
        emails.send_support_raised(
            email=settings.SUPPORT_EMAIL,
            reference=ticket.reference,
            subject=subject,
            organisation=organisation.name,
            body=body,
        )
    except Exception:
        log.exception("could not email the support alert for %s", ticket.reference)

    log.info("ticket %s raised by %s", ticket.reference, actor.email)
    return ticket


@transaction.atomic
def reply_to_ticket(
    *,
    ticket: SupportTicket,
    actor: User,
    body: str,
    internal: bool = False,
) -> SupportMessage:
    """
    Add a message to a ticket, from either side.

    ── THE internal FLAG ───────────────────────────────────────────────────────

    An internal note is written by staff about a client, knowing the client
    cannot see it. Only staff may set it — a client passing internal=True must
    not be able to write a note they then cannot see, and more importantly the
    flag must never be settable by the side it is hidden from.

    ── first_answered_at IS MEASURED, NOT PROMISED ─────────────────────────────

    Set the first time a staff member replies publicly, and never afterwards.
    Charter 03 §IV forbids stating a response time we have not tested; this is
    how we would eventually earn the right to state one.
    """
    body = body.strip()
    if not body:
        raise OperationsError("An empty reply is not a reply.", field="body")

    from_staff = bool(actor.is_staff)
    internal = bool(internal) and from_staff

    if ticket.status == SupportTicket.Status.RESOLVED and not from_staff:
        # A client replying to something we closed has reopened it, whatever we
        # thought. Silently discarding that is how a client concludes nobody is
        # listening.
        ticket.status = SupportTicket.Status.OPEN
        ticket.resolved_at = None

    message = SupportMessage.objects.create(
        ticket=ticket,
        author=actor,
        author_label=actor.full_name or actor.email,
        from_staff=from_staff,
        body=body,
        internal=internal,
    )

    fields = ["status", "resolved_at"]
    if from_staff and not internal:
        if ticket.first_answered_at is None:
            ticket.first_answered_at = timezone.now()
            fields.append("first_answered_at")
        ticket.status = SupportTicket.Status.ANSWERED
    elif not from_staff:
        ticket.status = SupportTicket.Status.OPEN

    ticket.save(update_fields=fields)

    if from_staff and not internal:
        _notify(
            users=_client_recipients(ticket.organisation),
            audience=Notification.Audience.CLIENT,
            kind=Notification.Kind.SUPPORT_REPLY,
            title=f"Reply on {ticket.subject}",
            body=body[:200],
            url="/support",
        )
        try:
            emails.send_support_reply(
                email=ticket.raised_by.email,
                reference=ticket.reference,
                subject=ticket.subject,
                body=body,
            )
        except Exception:
            log.exception("could not email the reply on %s", ticket.reference)

    elif not from_staff:
        _notify(
            users=_staff_recipients(),
            audience=Notification.Audience.STAFF,
            kind=Notification.Kind.SUPPORT_RAISED,
            title=f"{ticket.organisation.name} replied: {ticket.subject}",
            body=body[:200],
            url="/support",
        )

    return message


@transaction.atomic
def set_ticket_state(
    *,
    ticket: SupportTicket,
    actor: User,
    status: str | None = None,
    priority: str | None = None,
    assigned_to: User | None = None,
) -> SupportTicket:
    """Triage. Staff only — the permission is enforced by the view."""
    fields = []

    if status is not None:
        if status not in SupportTicket.Status.values:
            raise OperationsError("That is not a status we use.", field="status")
        ticket.status = status
        fields.append("status")
        ticket.resolved_at = (
            timezone.now() if status == SupportTicket.Status.RESOLVED else None
        )
        fields.append("resolved_at")

    if priority is not None:
        if priority not in SupportTicket.Priority.values:
            raise OperationsError("That is not a priority we use.", field="priority")
        ticket.priority = priority
        fields.append("priority")

    if assigned_to is not None:
        if not assigned_to.is_staff or not assigned_to.is_active:
            raise OperationsError(
                "Support is answered by Genmars staff with access.",
                field="assigned_to",
            )
        ticket.assigned_to = assigned_to
        fields.append("assigned_to")

    if fields:
        ticket.save(update_fields=fields)
    return ticket




# ─────────────────────────────────────────────────────────────────────────────
# The company's own billing identity
# ─────────────────────────────────────────────────────────────────────────────

# What a founder may change here. Written out rather than derived from the
# model so that adding a field to BillingProfile does not silently make it
# editable over HTTP — a new field becomes writable when somebody puts it in
# this list on purpose.
BILLING_FIELDS = (
    "legal_name",
    "email",
    "kra_pin",
    "postal_address",
    "mpesa_paybill",
    "mpesa_account_hint",
    "bank_details",
    "terms",
)


def set_billing_details(*, actor: User, values: dict) -> BillingProfile:
    """
    Update the billing profile, and record who changed what.

    ── WHY THIS IS A SERVICE AND NOT A ModelSerializer.save() ──────────────────

    Same reason as `set_tier_price`, only more so. These values are printed on
    documents clients pay against, and one of them — the paybill, or the bank
    details — decides which account the money lands in. A change here needs an
    author and a timestamp permanently attached, because "when did the paybill
    become this number, and who typed it" is a question that only ever gets
    asked in bad circumstances, and by then the form has long since forgotten.

    ── THE LOG RECORDS FIELD NAMES, NOT VALUES ────────────────────────────────

    `detail` carries which fields moved and nothing about what they moved to.
    A paybill is not a secret — it is printed on every invoice — but the log is
    the record used to INVESTIGATE a fraudulent change, and storing the new
    account details in it adds a second place to read them from while adding
    nothing to the investigation: the current values are one query away, and
    the point of this entry is who and when.

    ── NO-OP SAVES ARE NOT LOGGED ─────────────────────────────────────────────

    Opening the form and pressing save changes nothing and writes nothing. A
    log that fills with entries recording no change is one nobody reads, and
    this is a log whose entire value is that a line in it means something.
    """
    paybill = str(values.get("mpesa_paybill", "") or "").strip()
    if paybill and not paybill.isdigit():
        # The one field worth checking, because it is the one that decides
        # where the money goes. A paybill with a stray letter or space fails at
        # the till with no explanation, and the client's reasonable conclusion
        # is that Genmars sent them a broken invoice.
        raise OperationsError(
            "A paybill or till is digits only — no spaces or letters.",
            field="mpesa_paybill",
        )

    hint = str(values.get("mpesa_account_hint", "") or "").strip()
    if hint and "{number}" not in hint:
        # Without it every client types the same account reference, and no
        # payment can be matched to an invoice without a phone call — the exact
        # manual reconciliation this field exists to remove.
        raise OperationsError(
            "Include {number} — it becomes the invoice number, which is how a "
            "payment is matched to a bill.",
            field="mpesa_account_hint",
        )

    profile = BillingProfile.load()

    changed = []
    for field in BILLING_FIELDS:
        if field not in values:
            continue
        # Trimmed, because a trailing space on a paybill is invisible on the
        # screen where it was typed and wrong on every invoice after it.
        new = str(values[field] or "").strip()
        if new != (getattr(profile, field) or ""):
            setattr(profile, field, new)
            changed.append(field)

    if not changed:
        return profile

    profile.updated_by = actor
    # updated_at is auto_now, so it is saved whether or not it is listed.
    profile.save(update_fields=[*changed, "updated_by", "updated_at"])

    record(
        actor=actor,
        action=ActivityLog.Action.BILLING_CHANGED,
        subject="billing",
        summary=f"Billing details changed: {', '.join(changed)}",
        fields=changed,
    )
    return profile


# ═════════════════════════════════════════════════════════════════════════════
# The workroom — clocking in and out
# ═════════════════════════════════════════════════════════════════════════════

# How long a shift may stay open before we stop believing it.
#
# Eighteen hours, not twenty-four: the failure this catches is somebody
# clocking in on Monday morning and closing the laptop, and the tell is that
# the shift is still open on Tuesday. A twenty-four hour window would let a
# Monday 09:00 shift be closed at Tuesday 08:00 as a twenty-three hour day
# without anybody being asked about it.
STALE_SHIFT = timedelta(hours=18)


@transaction.atomic
def clock_in(*, actor: User, note: str = "") -> Shift:
    """
    Start a shift, for the requesting account and no other.

    There is no `person` argument and there must not be one. See Shift.
    """
    open_shift = Shift.objects.select_for_update().filter(
        person=actor, ended_at__isnull=True
    ).first()
    if open_shift is not None:
        raise OperationsError(
            f"You have been clocked in since "
            f"{timezone.localtime(open_shift.started_at):%H:%M on %-d %B}. "
            "Clock out first."
        )

    try:
        shift = Shift.objects.create(person=actor, started_note=(note or "").strip()[:200])
    except IntegrityError:
        # The partial unique index caught a double tap the SELECT above raced
        # past. Same refusal, because the same thing is true.
        raise OperationsError("You are already clocked in.")

    record(
        actor=actor,
        action=ActivityLog.Action.SHIFT_STARTED,
        subject="shift",
        summary=f"{actor.full_name or actor.email} clocked in",
        note=shift.started_note,
    )
    return shift


@transaction.atomic
def clock_out(*, actor: User, note: str = "", ended_at=None) -> Shift:
    """
    End the open shift.

    ── WHY A STALE SHIFT WILL NOT CLOSE AT "NOW" ───────────────────────────────

    Forgetting to clock out is the ordinary failure here, and closing such a
    shift at the current time writes a nineteen-hour day into the timesheet.
    One of those poisons every average and every total on the screen, and
    nothing about the record says it is wrong.

    So past STALE_SHIFT this refuses and asks when the person actually
    finished. The answer is a memory rather than a measurement, which is why
    `ended_late` is set: the row keeps the distinction instead of presenting
    both kinds of hour as the same fact.
    """
    shift = Shift.objects.select_for_update().filter(
        person=actor, ended_at__isnull=True
    ).first()
    if shift is None:
        raise OperationsError("You are not clocked in.")

    now = timezone.now()
    late = False

    if ended_at is None:
        if now - shift.started_at > STALE_SHIFT:
            raise OperationsError(
                "This shift has been open since "
                f"{timezone.localtime(shift.started_at):%H:%M on %-d %B} — long "
                "enough that it looks like a missed clock-out. When did you "
                "actually finish?",
                field="ended_at",
            )
        ended_at = now
    else:
        late = True
        if ended_at <= shift.started_at:
            raise OperationsError(
                "That is before the shift started.", field="ended_at"
            )
        if ended_at > now:
            raise OperationsError(
                "That is in the future. A shift is recorded after it happens.",
                field="ended_at",
            )

    shift.ended_at = ended_at
    shift.ended_note = (note or "").strip()[:200]
    shift.ended_late = late
    shift.save(update_fields=["ended_at", "ended_note", "ended_late"])

    hours, minutes = divmod(shift.minutes, 60)
    record(
        actor=actor,
        action=ActivityLog.Action.SHIFT_ENDED,
        subject="shift",
        summary=(
            f"{actor.full_name or actor.email} clocked out after "
            f"{hours}h {minutes:02d}m" + (" (entered afterwards)" if late else "")
        ),
        minutes=shift.minutes,
        entered_afterwards=late,
        note=shift.ended_note,
    )
    return shift


# ═════════════════════════════════════════════════════════════════════════════
# The decision register
# ═════════════════════════════════════════════════════════════════════════════


def next_decision_reference(today: date | None = None) -> str:
    year = (today or timezone.localdate()).year
    prefix = f"{REFERENCE_PREFIX}-DEC-{year}-"
    used = Decision.objects.filter(reference__startswith=prefix).count()
    return f"{prefix}{used + 1:04d}"


@transaction.atomic
def record_decision(
    *,
    actor: User,
    title: str,
    context: str,
    decision: str,
    options: str = "",
    consequences: str = "",
    revisit_when: str = "",
    status: str = Decision.Status.DECIDED,
    decided_on: date | None = None,
    supersedes: Decision | None = None,
) -> Decision:
    """
    Write a decision down.

    `context` is required and the refusal says why. It is the one field that
    stops being obvious first — the constraint that forced the choice is gone
    within weeks, and without it the entry reads as a preference somebody had.
    """
    title = (title or "").strip()
    context = (context or "").strip()
    body = (decision or "").strip()

    if not title:
        raise OperationsError("What was decided? One line.", field="title")
    if not body:
        raise OperationsError("Say what we are actually doing.", field="decision")
    if not context:
        raise OperationsError(
            "What was true at the time that forced a choice? Without it, this "
            "reads in six months as an arbitrary preference — which is how a "
            "decision gets undone and the original problem comes back.",
            field="context",
        )
    if status not in Decision.Status.values:
        raise OperationsError("That is not a status.", field="status")
    if status in {Decision.Status.SUPERSEDED, Decision.Status.REVERSED}:
        raise OperationsError(
            "A new entry is proposed or decided. Superseding and reversing are "
            "things that happen to an existing one.",
            field="status",
        )

    decided = status == Decision.Status.DECIDED
    if supersedes is not None and not decided:
        raise OperationsError(
            "A proposal does not supersede anything yet. It replaces "
            f"{supersedes.reference} when it is decided.",
            field="supersedes",
        )
    if supersedes is not None and supersedes.status != Decision.Status.DECIDED:
        raise OperationsError(
            f"{supersedes.reference} is {supersedes.get_status_display().lower()}, "
            "so there is nothing in force to replace.",
            field="supersedes",
        )

    for attempt in range(5):
        try:
            with transaction.atomic():
                entry = Decision.objects.create(
                    reference=next_decision_reference(),
                    title=title[:200],
                    context=context,
                    options=(options or "").strip(),
                    decision=body,
                    consequences=(consequences or "").strip(),
                    revisit_when=(revisit_when or "").strip()[:300],
                    status=status,
                    decided_by=actor if decided else None,
                    decided_by_label=(actor.full_name or actor.email) if decided else "",
                    decided_on=(decided_on or timezone.localdate()) if decided else None,
                    supersedes=supersedes,
                )
            break
        except IntegrityError:
            if attempt == 4:
                raise
    else:  # pragma: no cover
        raise OperationsError("Could not allocate a decision reference.")

    if supersedes is not None:
        supersedes.status = Decision.Status.SUPERSEDED
        supersedes.save(update_fields=["status"])
        record(
            actor=actor,
            action=ActivityLog.Action.DECISION_SUPERSEDED,
            subject=supersedes.reference,
            summary=f"{supersedes.reference} superseded by {entry.reference}",
            replaced_by=entry.reference,
        )

    record(
        actor=actor,
        action=(
            ActivityLog.Action.DECISION_MADE
            if decided
            else ActivityLog.Action.DECISION_RECORDED
        ),
        subject=entry.reference,
        summary=f"{entry.reference} {'decided' if decided else 'proposed'}: {entry.title}",
    )
    return entry


@transaction.atomic
def revise_decision(*, entry: Decision, actor: User, **values) -> Decision:
    """
    Edit a PROPOSAL. Refuses anything else.

    A decided entry has been relied on — quoted in a contract, built against,
    told to a client — and editing it rewrites what the company was working
    from. Supersede it instead; the original stays readable, which is where
    the value is.
    """
    if not entry.is_open_to_edits:
        raise OperationsError(
            f"{entry.reference} is {entry.get_status_display().lower()} and is not "
            "edited. Record a new decision that supersedes it — the original "
            "stays readable, which is most of the point of keeping this.",
        )

    editable = ("title", "context", "options", "decision", "consequences", "revisit_when")
    changed = []
    for field in editable:
        if field not in values:
            continue
        new = (values[field] or "").strip()
        if new != getattr(entry, field):
            setattr(entry, field, new)
            changed.append(field)

    if not (entry.title or "").strip():
        raise OperationsError("What was decided? One line.", field="title")
    if not (entry.context or "").strip():
        raise OperationsError("A decision without its context teaches nothing.", field="context")
    if not (entry.decision or "").strip():
        raise OperationsError("Say what we are actually doing.", field="decision")

    if changed:
        entry.save(update_fields=[*changed, "updated_at"])
    return entry


@transaction.atomic
def decide(*, entry: Decision, actor: User, decided_on: date | None = None) -> Decision:
    """Move a proposal to decided. From here it is superseded, never edited."""
    if entry.status != Decision.Status.PROPOSED:
        raise OperationsError(
            f"{entry.reference} is already {entry.get_status_display().lower()}."
        )
    entry.status = Decision.Status.DECIDED
    entry.decided_by = actor
    entry.decided_by_label = actor.full_name or actor.email
    entry.decided_on = decided_on or timezone.localdate()
    entry.save(update_fields=["status", "decided_by", "decided_by_label", "decided_on", "updated_at"])

    record(
        actor=actor,
        action=ActivityLog.Action.DECISION_MADE,
        subject=entry.reference,
        summary=f"{entry.reference} decided: {entry.title}",
    )
    return entry


@transaction.atomic
def reverse_decision(*, entry: Decision, actor: User, reason: str) -> Decision:
    """
    We were wrong. Say so, in the register, with the reason attached.

    The reason is required. A reversal without one is the single entry in this
    register that teaches nothing — it removes a decision from force and leaves
    the next person to make the same one again.
    """
    reason = (reason or "").strip()
    if not reason:
        raise OperationsError(
            "Why is it being reversed? A reversal with no reason leaves the "
            "next person free to make the same decision again.",
            field="reason",
        )
    if entry.status not in {Decision.Status.PROPOSED, Decision.Status.DECIDED}:
        raise OperationsError(
            f"{entry.reference} is already {entry.get_status_display().lower()}."
        )

    entry.status = Decision.Status.REVERSED
    entry.reversal_reason = reason
    entry.save(update_fields=["status", "reversal_reason", "updated_at"])

    record(
        actor=actor,
        action=ActivityLog.Action.DECISION_REVERSED,
        subject=entry.reference,
        summary=f"{entry.reference} reversed: {reason[:180]}",
    )
    return entry


# ═════════════════════════════════════════════════════════════════════════════
# The client record
# ═════════════════════════════════════════════════════════════════════════════

PROFILE_FIELDS = (
    "what_they_do",
    "website",
    "contact_name",
    "contact_role",
    "contact_phone",
    "contact_email",
    "preferred_channel",
    "client_since",
    "notes",
    "may_be_named",
    "permission_note",
)


@transaction.atomic
def set_client_profile(*, organisation: Organisation, actor: User, values: dict) -> ClientProfile:
    """
    Update who a client is.

    ── WHY `may_be_named` IS SINGLED OUT IN THE LOG ────────────────────────────

    Charter 04 §V lets us name a client only with WRITTEN permission, and
    Charter 04 §IV forbids publishing a claim we cannot evidence. Flipping this
    flag is therefore not an edit to a contact card — it is the assertion that
    a specific person agreed to something, and `/work/` on the marketing site
    is gated on exactly that assertion.

    So turning it ON demands the evidence be named. A tick with nothing behind
    it is how a client ends up on a public page having never said yes.
    """
    profile = ClientProfile.objects.select_for_update().get_or_create(
        organisation=organisation
    )[0]

    turning_on = bool(values.get("may_be_named")) and not profile.may_be_named
    note = str(values.get("permission_note", profile.permission_note) or "").strip()
    if turning_on and not note:
        raise OperationsError(
            "Say where the written permission is — the file, or the date of the "
            "email. Charter 04 §V allows naming a client only with written "
            "permission, and a tick with nothing behind it is how somebody ends "
            "up on a public page having never agreed to it.",
            field="permission_note",
        )

    changed = []
    for field in PROFILE_FIELDS:
        if field not in values:
            continue
        new = values[field]
        if isinstance(new, str):
            new = new.strip()
        if new != getattr(profile, field):
            setattr(profile, field, new)
            changed.append(field)

    if not changed:
        return profile

    profile.save(update_fields=[*changed, "updated_at"])
    record(
        actor=actor,
        action=ActivityLog.Action.CLIENT_PROFILE_CHANGED,
        subject=organisation.name,
        organisation=organisation,
        summary=f"Client details changed: {', '.join(changed)}",
        fields=changed,
    )
    if "may_be_named" in changed:
        # Its own line, because "we may now name this client publicly" is a
        # different fact from "somebody edited a phone number", and the log is
        # where a question about a published page gets answered.
        record(
            actor=actor,
            action=ActivityLog.Action.CLIENT_PROFILE_CHANGED,
            subject=organisation.name,
            organisation=organisation,
            summary=(
                f"{organisation.name} may {'now' if profile.may_be_named else 'NO LONGER'} "
                f"be named publicly"
                + (f" — {profile.permission_note}" if profile.may_be_named else "")
            ),
        )
    return profile


HOSTING_FIELDS = (
    "kind",
    "identifier",
    "provider",
    "account_holder",
    "renews_on",
    "auto_renew",
    "annual_cost_kes",
    "annual_charge_kes",
    "notes",
    "system",
)


@transaction.atomic
def record_hosting(
    *, organisation: Organisation, actor: User, values: dict
) -> HostingArrangement:
    """Write down something we run or renew for a client."""
    identifier = str(values.get("identifier", "") or "").strip()
    if not identifier:
        raise OperationsError(
            "What is it? The domain, the plan, the mailbox.", field="identifier"
        )

    arrangement = HostingArrangement.objects.create(
        organisation=organisation,
        kind=values.get("kind") or HostingArrangement.Kind.OTHER,
        identifier=identifier[:200],
        provider=str(values.get("provider", "") or "").strip()[:120],
        account_holder=values.get("account_holder")
        or HostingArrangement.Holder.CLIENT,
        renews_on=values.get("renews_on"),
        auto_renew=bool(values.get("auto_renew", False)),
        annual_cost_kes=values.get("annual_cost_kes"),
        annual_charge_kes=values.get("annual_charge_kes"),
        notes=str(values.get("notes", "") or "").strip(),
        system=values.get("system"),
    )

    record(
        actor=actor,
        action=ActivityLog.Action.HOSTING_RECORDED,
        subject=identifier,
        organisation=organisation,
        summary=(
            f"{arrangement.get_kind_display()} recorded for {organisation.name}: "
            f"{identifier}"
            + (f", renews {arrangement.renews_on}" if arrangement.renews_on else "")
        ),
        account_holder=arrangement.account_holder,
    )
    return arrangement


@transaction.atomic
def update_hosting(
    *, arrangement: HostingArrangement, actor: User, values: dict
) -> HostingArrangement:
    changed = []
    for field in HOSTING_FIELDS:
        if field not in values:
            continue
        new = values[field]
        if isinstance(new, str):
            new = new.strip()
        if new != getattr(arrangement, field):
            setattr(arrangement, field, new)
            changed.append(field)

    if not changed:
        return arrangement

    arrangement.save(update_fields=[*changed, "updated_at"])
    record(
        actor=actor,
        action=ActivityLog.Action.HOSTING_CHANGED,
        subject=arrangement.identifier,
        organisation=arrangement.organisation,
        summary=f"{arrangement.identifier} updated: {', '.join(changed)}",
        fields=changed,
    )
    return arrangement


@transaction.atomic
def retire_hosting(
    *, arrangement: HostingArrangement, actor: User, reason: str = ""
) -> HostingArrangement:
    """
    Stop running it. The row stays.

    Deleting would remove the evidence that we ever held a client's domain,
    which is the one question worth being able to answer years later.
    """
    if arrangement.retired_at is not None:
        raise OperationsError(f"{arrangement.identifier} is already retired.")

    arrangement.retired_at = timezone.now()
    arrangement.save(update_fields=["retired_at", "updated_at"])
    record(
        actor=actor,
        action=ActivityLog.Action.HOSTING_RETIRED,
        subject=arrangement.identifier,
        organisation=arrangement.organisation,
        summary=f"{arrangement.identifier} retired"
        + (f": {reason.strip()}" if reason.strip() else ""),
    )
    return arrangement


@transaction.atomic
def log_contact(
    *,
    organisation: Organisation,
    actor: User,
    channel: str,
    direction: str,
    summary: str,
    detail: str = "",
    with_whom: str = "",
    happened_at=None,
    order: Order | None = None,
    follow_up: str = "",
    follow_up_by: date | None = None,
    create_task: bool = True,
) -> ContactLogEntry:
    """
    Write down a conversation.

    ── A FOLLOW-UP WITH NO DATE IS A WISH ──────────────────────────────────────

    If we said we would do something, the date is required. Not to be strict:
    an undated follow-up cannot appear on any queue, cannot be overdue, and
    therefore behaves exactly like not having written it down at all — while
    looking like it was handled. That is worse than the blank field, because it
    buys the feeling of having recorded it.
    """
    summary = (summary or "").strip()
    if not summary:
        raise OperationsError(
            "What was it about? One line.", field="summary"
        )
    if channel not in ContactLogEntry.Channel.values:
        raise OperationsError("How did you speak to them?", field="channel")
    if direction not in ContactLogEntry.Direction.values:
        raise OperationsError("Who contacted whom?", field="direction")

    follow_up = (follow_up or "").strip()
    if follow_up and follow_up_by is None:
        raise OperationsError(
            "By when? An undated follow-up never reaches a queue and never goes "
            "overdue, so it behaves exactly like not writing it down — while "
            "feeling like you did.",
            field="follow_up_by",
        )

    happened_at = happened_at or timezone.now()
    if happened_at > timezone.now() + timedelta(minutes=5):
        raise OperationsError(
            "That is in the future. A conversation is recorded after it happens.",
            field="happened_at",
        )
    if order is not None and order.organisation_id != organisation.id:
        # Would file one client's conversation under another client's project.
        raise OperationsError("That order belongs to a different client.", field="order")

    entry = ContactLogEntry.objects.create(
        organisation=organisation,
        order=order,
        channel=channel,
        direction=direction,
        happened_at=happened_at,
        with_whom=(with_whom or "").strip()[:200],
        summary=summary[:300],
        detail=(detail or "").strip(),
        recorded_by=actor,
        recorded_by_label=actor.full_name or actor.email,
        follow_up=follow_up[:300],
        follow_up_by=follow_up_by,
    )

    record(
        actor=actor,
        action=ActivityLog.Action.CONTACT_LOGGED,
        subject=organisation.name,
        organisation=organisation,
        summary=f"{entry.get_channel_display()} with {organisation.name}: {summary[:180]}",
        follow_up=bool(follow_up),
    )

    if create_task:
        _task_from_contact(entry=entry, actor=actor)

    return entry


def _task_from_contact(*, entry: ContactLogEntry, actor: User) -> Task | None:
    """
    Turn a conversation into work on the board, where the board can be seen.

    ══════════════════════════════════════════════════════════════════════════
    NOT EVERY CONVERSATION, AND THAT RESTRAINT IS THE DESIGN.

    Making every logged message a task is the obvious version of this and it
    fails within a fortnight: the board fills with "called about the invoice"
    rows nobody will ever tick off, the real work is buried among them, and
    people stop opening it. A board that is mostly noise is worse than no
    board, because the noise is indistinguishable from work at a glance.

    So a task is created when the conversation produced one of two things:

      · A FOLLOW-UP. We said we would do something, by a date. That is not a
        record of a chat, it is an obligation with a deadline, and it is the
        commonest thing this company drops.

      · A CONVERSATION ABOUT A SPECIFIC PIECE OF WORK. Talking about an order
        almost always means something changed about it, and the caller is the
        only person who knows what. Left as a log entry it is visible to
        whoever goes looking; as a task it is visible to everyone.

    A conversation attached to neither is a record, and inventing work for it
    would be inventing work.
    ══════════════════════════════════════════════════════════════════════════

    ── IT IS ASSIGNED TO WHOEVER LOGGED IT ─────────────────────────────────────

    They had the conversation, so they are the only person who could act on it
    today. It is theirs until somebody reassigns it — and assigning to yourself
    needs no permission, which is what lets any staff account log a call
    without a founder in the loop.
    """
    if not entry.follow_up and entry.order_id is None:
        return None

    # The promise if there was one; otherwise the conversation itself, marked
    # as needing a decision rather than pretending to be an instruction.
    if entry.follow_up:
        title = entry.follow_up
        detail = f"Promised on {timezone.localtime(entry.happened_at):%-d %B}."
    else:
        title = f"Follow up: {entry.summary}"
        detail = "Raised on a call about this work. Close it if nothing is needed."

    if entry.detail:
        detail = f"{detail}\n\n{entry.detail}"

    task = Task.objects.create(
        title=title[:200],
        detail=detail,
        assignee=actor,
        assigned_by=actor,
        organisation=entry.organisation,
        order=entry.order,
        contact=entry,
        due_on=entry.follow_up_by,
        # A promise with a date outranks a note to self about a call.
        priority=Task.Priority.HIGH if entry.follow_up else Task.Priority.NORMAL,
    )

    record(
        actor=actor,
        action=ActivityLog.Action.TASK_ASSIGNED,
        subject=entry.order.reference if entry.order else entry.organisation.name,
        organisation=entry.organisation,
        summary=f"{task.title} — from a {entry.get_channel_display().lower()} with {entry.organisation.name}",
        from_contact=True,
    )
    return task


@transaction.atomic
def clear_follow_up(*, entry: ContactLogEntry, actor: User, note: str = "") -> ContactLogEntry:
    """
    Mark a promise kept.

    Does not edit the entry it clears. The original text of what was promised
    stays exactly as it was written — a log that rewrote the promise when it
    was fulfilled could never answer whether what we did was what we said.
    """
    if not entry.follow_up:
        raise OperationsError("There is nothing owed on that entry.")
    if entry.cleared_at is not None:
        raise OperationsError("That follow-up is already cleared.")

    entry.cleared_at = timezone.now()
    entry.cleared_by = actor
    entry.save(update_fields=["cleared_at", "cleared_by"])

    # The task this promise created is done too. Leaving it open would mean
    # marking the same thing finished in two places, and the second one is the
    # one people forget — so the board slowly fills with work that was
    # completed weeks ago, which is how a board stops being believed.
    entry.tasks.exclude(status=Task.Status.DONE).update(
        status=Task.Status.DONE, done_at=timezone.now()
    )

    record(
        actor=actor,
        action=ActivityLog.Action.FOLLOW_UP_CLEARED,
        subject=entry.organisation.name,
        organisation=entry.organisation,
        summary=f"Follow-up done for {entry.organisation.name}: {entry.follow_up[:160]}"
        + (f" — {note.strip()[:100]}" if note.strip() else ""),
    )
    return entry


@transaction.atomic
def attach_to_contact(
    *, entry: ContactLogEntry, actor: User, upload, caption: str = ""
) -> ContactAttachment:
    """
    Store a file that came out of a conversation.

    Everything about WHAT may be stored lives in portal/attachments.py, which
    reads the bytes rather than believing the browser. This function does the
    bookkeeping around that decision and nothing else.
    """
    from portal import attachments

    content_type, extension = attachments.inspect(upload)

    # The name is kept for display only, and trimmed of any path the browser
    # sent. Some send `C:\Users\...\photo.jpg`; rendering that is untidy, and
    # letting it anywhere near a filesystem call is the bug this avoids.
    original = (getattr(upload, "name", "") or "file").replace("\\", "/").split("/")[-1]

    attachment = ContactAttachment(
        entry=entry,
        original_name=original[:255],
        content_type=content_type,
        size_bytes=upload.size,
        caption=(caption or "").strip()[:300],
        uploaded_by=actor,
        uploaded_by_label=actor.full_name or actor.email,
    )
    # `save` on the FileField runs attachment_path, which uses the extension we
    # decided rather than the one on the upload. Passing `upload.name` here
    # would put the client's string back into the path.
    attachment.file.save(f"upload{extension}", upload, save=False)
    attachment.save()

    record(
        actor=actor,
        action=ActivityLog.Action.CONTACT_LOGGED,
        subject=entry.organisation.name,
        organisation=entry.organisation,
        summary=f"File attached to a conversation with {entry.organisation.name}: {original[:120]}",
        bytes=upload.size,
    )
    return attachment


# ── creating an order directly, and telling the client ───────────────────────


@transaction.atomic
def create_order(
    *,
    organisation: Organisation,
    actor: User,
    title: str,
    scope: str,
    exclusions: str = "",
    contact: User | None = None,
    target_date: date | None = None,
    service: Service | None = None,
    from_contact: ContactLogEntry | None = None,
    tell_client: bool = True,
    kind: str = Order.Kind.PROJECT,
    started_on: date | None = None,
    completed_on: date | None = None,
    status: str | None = None,
) -> Order:
    """
    Open an order for a client we already have, with no enquiry behind it.

    ══════════════════════════════════════════════════════════════════════════
    THIS DOES NOT START WORK, AND THE CLIENT IS NOT TOLD THAT IT DOES.

    Charter 02 §I puts a signed statement of work before delivery. An existing
    client asking for a feature over WhatsApp has not signed anything, so what
    this creates is an order in SCOPING — a written record of what was asked
    for, with the scope and the exclusions stated, which is what Charter 05 §I
    requires to exist BEFORE work begins.

    The email says exactly that. It is the single most tempting place in this
    system to write "we've started on your booking feature", and doing so would
    be a commitment made by a notification rather than by a contract.
    ══════════════════════════════════════════════════════════════════════════

    ── WHY THE CLIENT IS TOLD AT ALL ───────────────────────────────────────────

    Because otherwise the record of what we understood them to want sits in our
    system, unread by the only person who can say it is wrong. The whole value
    of writing scope down before work is that the client gets to disagree with
    it while disagreeing is still cheap.
    """
    if not title.strip():
        raise OperationsError("Give the order a title.", field="title")

    if service is not None:
        scope = scope.strip() or service.default_scope
        exclusions = exclusions.strip() or service.default_exclusions

    if not scope.strip():
        raise OperationsError(
            "An order needs a scope. Charter 05 §I — fixed scope, in writing, "
            "before work begins. This is that writing.",
            field="scope",
        )

    lead = contact or actor
    if not lead.is_staff:
        raise OperationsError(
            "The named contact must be a Genmars account.", field="contact"
        )
    if from_contact is not None and from_contact.organisation_id != organisation.id:
        raise OperationsError("That conversation belongs to a different client.")

    if kind not in Order.Kind.values:
        raise OperationsError("That is not a kind of work.", field="kind")

    # ── work that happened before it was written down ───────────────────────
    #
    # Recognised by a start date in the past rather than by a checkbox: a
    # checkbox is a thing somebody forgets to tick, and the date is already
    # required to describe the work honestly. See Order.recorded_retrospectively
    # for what the flag then stops from happening.
    today = timezone.localdate()
    retrospective = bool(started_on and started_on < today) or bool(completed_on)

    if completed_on and started_on and completed_on < started_on:
        raise OperationsError(
            "It cannot have finished before it started.", field="completed_on"
        )
    if completed_on and completed_on > today:
        raise OperationsError(
            "That is in the future. A completion date is recorded after the fact.",
            field="completed_on",
        )

    if status is not None and status not in Order.Status.values:
        raise OperationsError("That is not an order status.", field="status")
    if status is None:
        # Past work that finished is DELIVERED, not SCOPING. An order recorded
        # as scoping for something delivered last year would sit on the
        # delivery board forever, waiting for gates nobody is going to meet.
        status = Order.Status.DELIVERED if completed_on else Order.Status.SCOPING

    last_error: IntegrityError | None = None
    for _ in range(_MAX_REFERENCE_ATTEMPTS):
        try:
            with transaction.atomic():
                order = Order.objects.create(
                    organisation=organisation,
                    reference=next_reference(),
                    title=title.strip(),
                    scope=scope.strip(),
                    exclusions=exclusions.strip(),
                    status=status,
                    contact=lead,
                    target_date=target_date,
                    service=service,
                    kind=kind,
                    started_on=started_on,
                    completed_on=completed_on,
                    recorded_retrospectively=retrospective,
                )
            break
        except IntegrityError as exc:  # pragma: no cover - needs a real race
            last_error = exc
    else:  # pragma: no cover
        raise OperationsError("Could not allocate an order reference. Try again.") from last_error

    # Gates describe a project being built. Retrospective work has already been
    # built, and a retainer is never "done" — six unmet gates against either is
    # a delivery board describing something that is not happening.
    if not retrospective and kind == Order.Kind.PROJECT:
        create_delivery_gates(order=order)

    # Close the loop: the conversation this came out of now points at the work.
    if from_contact is not None and from_contact.order_id is None:
        from_contact.order = order
        from_contact.save(update_fields=["order"])

    record(
        actor=actor,
        action=ActivityLog.Action.ENQUIRY_CONVERTED,
        subject=order.reference,
        organisation=organisation,
        summary=f"{order.reference} opened for {organisation.name}: {order.title}",
        direct=True,
    )

    # Never for past work. "This is what we understood you asked for, nothing
    # has started yet" is a lie about something delivered a year ago, and it
    # would arrive in the client's inbox looking like we had lost track of
    # what we had already done for them.
    if tell_client and not retrospective:
        notify_order_opened(order)

    return order


def notify_order_opened(order: Order) -> None:
    """
    Put it in their dashboard and in their inbox.

    Both, and for different reasons. The dashboard is where it lives and can be
    re-read; the email is what actually reaches somebody who is not going to
    sign in today. Neither is the record — the order is.
    """
    _notify(
        users=_client_recipients(order.organisation),
        audience=Notification.Audience.CLIENT,
        kind=Notification.Kind.ORDER_UPDATE,
        title=f"{order.reference} — {order.title}",
        body="We have written down what we understood. Please check it.",
        url=f"/dashboard/{order.reference}",
    )
    _email_order_opened(order)


def _email_order_opened(order: Order) -> None:
    """
    Email the people at this client who take updates.

    Same two exclusions as a progress note, for the same reasons:
    `receives_updates` off means they asked not to hear about this, and an
    unverified address is one nobody has proved they read — sending a client's
    scope and price to it would be sending it to whoever owns that mailbox.
    """
    recipients = (
        Membership.objects.filter(
            organisation=order.organisation, receives_updates=True
        )
        .select_related("user")
        .exclude(user__email_verified_at__isnull=True)
    )

    for membership in recipients:
        try:
            emails.send_order_opened(
                email=membership.user.email,
                reference=order.reference,
                title=order.title,
                scope=order.scope,
                exclusions=order.exclusions,
                target_date=order.target_date.isoformat() if order.target_date else "",
                contact=order.contact.full_name or order.contact.email,
            )
        except Exception:
            # A failed email must not roll back the order. The order is the
            # fact; the email is an account of it, and the notification in the
            # dashboard has already landed.
            log.exception("could not email %s about %s", membership.user.email, order.reference)


# ── clients: the rest of the lifecycle ───────────────────────────────────────


@transaction.atomic
def rename_organisation(*, organisation: Organisation, actor: User, name: str) -> Organisation:
    """
    Change a client's name.

    ── THIS USED TO REWRITE HISTORY, AND NOW DOES NOT ──────────────────────────

    Invoice documents read the billed-to line from `Invoice.billed_to_name`, a
    copy taken when the invoice was issued. Before that field existed they read
    `organisation.name` live, which meant a rename silently changed the "To:"
    line on every invoice already sent and paid — so our copy and the client's
    copy of the same numbered document would disagree about who was billed.

    That is why renaming is offered at all rather than being refused: it is a
    correction people genuinely need (a typo, a rebrand, a change of legal
    entity), and it is safe now that the documents hold their own copy.
    """
    name = (name or "").strip()
    if not name:
        raise OperationsError("Give the client a name.", field="name")
    if name == organisation.name:
        return organisation

    clash = Organisation.objects.filter(name__iexact=name).exclude(pk=organisation.pk).first()
    if clash:
        raise OperationsError(f"{clash.name} already exists.", field="name")

    was = organisation.name
    organisation.name = name
    organisation.save(update_fields=["name"])

    record(
        actor=actor,
        action=ActivityLog.Action.CLIENT_RENAMED,
        subject=name,
        organisation=organisation,
        summary=f"{was} renamed to {name}",
        was=was,
    )
    return organisation


@transaction.atomic
def archive_organisation(
    *, organisation: Organisation, actor: User, reason: str = ""
) -> Organisation:
    """
    Stop working with a client without erasing them.

    ── AN UNPAID INVOICE BLOCKS THIS ───────────────────────────────────────────

    Archiving hides a client from the screens people work in, and a hidden
    client with money outstanding is money nobody chases. That is not a
    hypothetical tidiness concern: "we stopped working with them" and "they
    never paid the last invoice" are the same conversation more often than not.

    Voiding the invoice or recording the payment are both one click away, and
    either is an honest answer. Hiding it is not.
    """
    if organisation.is_archived:
        raise OperationsError(f"{organisation.name} is already archived.")

    # ISSUED is the only unsettled state — PAID and VOID are both resolved.
    # Overdue is a fact about a date rather than a status, so an invoice that
    # is late is simply still ISSUED and is caught here too.
    outstanding = Invoice.objects.filter(
        organisation=organisation, status=Invoice.Status.ISSUED
    )
    if outstanding.exists():
        numbers = ", ".join(outstanding.values_list("number", flat=True)[:5])
        raise OperationsError(
            f"{organisation.name} has unpaid invoices ({numbers}). Archiving "
            "hides them from every screen, and a hidden client with money "
            "outstanding is money nobody chases. Record the payment, or void "
            "the invoice if it is not owed."
        )

    organisation.archived_at = timezone.now()
    organisation.archived_reason = (reason or "").strip()[:300]
    organisation.save(update_fields=["archived_at", "archived_reason"])

    record(
        actor=actor,
        action=ActivityLog.Action.CLIENT_ARCHIVED,
        subject=organisation.name,
        organisation=organisation,
        summary=f"{organisation.name} archived"
        + (f": {organisation.archived_reason}" if organisation.archived_reason else ""),
    )
    return organisation


@transaction.atomic
def restore_organisation(*, organisation: Organisation, actor: User) -> Organisation:
    if not organisation.is_archived:
        raise OperationsError(f"{organisation.name} is not archived.")

    organisation.archived_at = None
    organisation.archived_reason = ""
    organisation.save(update_fields=["archived_at", "archived_reason"])

    record(
        actor=actor,
        action=ActivityLog.Action.CLIENT_RESTORED,
        subject=organisation.name,
        organisation=organisation,
        summary=f"{organisation.name} restored",
    )
    return organisation


# What must not exist for a client to be genuinely deletable. Each of these is
# either a record we are required to keep or somebody's work.
# Reverse accessor names, verified against Organisation._meta — a typo here
# would silently stop blocking on that relation, and the first thing anybody
# noticed would be a cascade that took an invoice with it. There is a test
# that every name in this tuple is a real relation.
ATTACHMENTS_BLOCKING_DELETE = (
    ("orders", "order"),
    ("invoices", "invoice"),
    ("enquiries", "enquiry"),
    ("tickets", "support request"),
    ("offers", "offer"),
    ("incidents", "incident"),
    ("systems", "system we run"),
    ("contact_log", "recorded conversation"),
    ("memberships", "person with access"),
    ("hosting", "hosting arrangement"),
)


@transaction.atomic
def delete_organisation(*, organisation: Organisation, actor: User) -> str:
    """
    Really delete a client. Only ever the duplicate typed in twice.

    ══════════════════════════════════════════════════════════════════════════
    THIS REFUSES THE MOMENT ANYTHING IS ATTACHED, AND THAT IS THE FEATURE.

    Organisation cascades. A delete that went through would take orders,
    invoices, contracts and support threads with it — including invoices that
    were issued, sent and paid, which are accounting records, and a support
    thread that is somebody's evidence of what they were promised.

    None of that is ours to remove because a relationship ended. The answer for
    a client we no longer work with is `archive_organisation`, which hides them
    and keeps every word.

    What is left for this function is the honest case: a name typed twice, five
    minutes ago, with nothing hanging off it.
    ══════════════════════════════════════════════════════════════════════════
    """
    blockers = []
    for relation, noun in ATTACHMENTS_BLOCKING_DELETE:
        manager = getattr(organisation, relation, None)
        if manager is None:
            continue
        count = manager.count()
        if count:
            blockers.append(f"{count} {noun}{'s' if count != 1 else ''}")

    if blockers:
        raise OperationsError(
            f"{organisation.name} has {', '.join(blockers)}. Deleting would take "
            "all of it, including records we are required to keep. Archive them "
            "instead — it hides them from every screen and keeps every word."
        )

    name = organisation.name
    # The log entry FIRST: its organisation FK is SET_NULL, so writing it after
    # the delete would lose the link, and writing it at all after the row is
    # gone would be a log entry about something that never existed.
    record(
        actor=actor,
        action=ActivityLog.Action.CLIENT_DELETED,
        subject=name,
        summary=f"{name} deleted — nothing was attached to it",
    )
    organisation.delete()
    return name
