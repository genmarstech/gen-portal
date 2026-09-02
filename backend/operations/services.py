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
from decimal import Decimal

from django.conf import settings
from django.db import IntegrityError, models, transaction
from django.utils import timezone

from accounts import emails, identity
from accounts.models import EmailCode, Membership, Organisation, User
from portal import mpesa
from portal.models import (
    Blocker,
    Contract,
    DeliveryGate,
    Enquiry,
    Invoice,
    Milestone,
    MpesaPayment,
    Order,
    ProgressNote,
    Service,
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

    log.info("invoice %s issued for %s by %s", invoice.number, order.reference, actor.email)
    return invoice


@transaction.atomic
def record_payment(
    *,
    invoice: Invoice,
    actor: User,
    paid_on: date | None = None,
    reference: str = "",
) -> Invoice:
    """
    Write down that money arrived. It does not move any.

    See Invoice's docstring — Genmars processes no payments, and this must not
    imply otherwise. The reference is what makes the row checkable against a
    bank statement, so it is asked for rather than optional-in-practice.
    """
    if invoice.status == Invoice.Status.VOID:
        raise OperationsError(
            f"{invoice.number} was voided. A voided invoice is not owed, so a "
            "payment against it needs a new invoice first.",
        )
    if invoice.status == Invoice.Status.PAID:
        raise OperationsError(f"{invoice.number} is already recorded as paid.")

    reference = reference.strip()
    if not reference:
        raise OperationsError(
            "Record the payment reference — the M-Pesa code or bank reference. "
            "Without it this row cannot be checked against the account.",
            field="reference",
        )

    paid_on = paid_on or timezone.localdate()
    if paid_on < invoice.issued_on:
        raise OperationsError(
            "That payment date is before the invoice was issued.",
            field="paid_on",
        )

    invoice.status = Invoice.Status.PAID
    invoice.paid_on = paid_on
    invoice.payment_reference = reference
    invoice.recorded_by = actor
    invoice.save(
        update_fields=["status", "paid_on", "payment_reference", "recorded_by"]
    )

    if invoice.milestone_id:
        invoice.milestone.mark_paid()

    log.info("payment recorded on %s by %s", invoice.number, actor.email)
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

    invoice.status = Invoice.Status.PAID
    invoice.paid_on = timezone.localdate()
    invoice.payment_reference = payment.receipt
    invoice.save(update_fields=["status", "paid_on", "payment_reference"])

    if invoice.milestone_id:
        invoice.milestone.mark_paid()

    log.info("mpesa %s paid %s", payment.receipt, invoice.number)
    return payment
