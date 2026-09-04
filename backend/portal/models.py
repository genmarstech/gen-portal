"""
Portal — what a client actually sees.

Every model here maps to a clause of Charter 05, the Client Charter. That is not
decoration: the charter is the promise, and this is the surface that makes it
visible rather than asserted.

  Order         §I  "a fixed scope and a fixed price, with exclusions stated in
                     writing before work begins"
  ProgressNote  §I  "a written progress update every week"
                §III "same day each week, even when progress is thin"
  Milestone     §VI  deposit, milestones, final payment on acceptance

DELIBERATELY NOT HERE in v1: change requests, invoicing, file storage,
messaging. Each is a real workflow with money or dates attached and deserves its
own release.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

from accounts.models import Organisation


class Order(models.Model):
    """
    An engagement. CREATED BY STAFF ONLY.

    There is no client-facing endpoint that creates one, on purpose: Charter 02
    §I gives qualification to the commercial partners and the capacity veto to
    the founder. An order exists when a SOW is signed, not when someone fills in
    a form.
    """

    class Status(models.TextChoices):
        SCOPING = "scoping", "Scoping"
        ACTIVE = "active", "In progress"
        REVIEW = "review", "In review"
        DELIVERED = "delivered", "Delivered"
        CLOSED = "closed", "Closed"

    organisation = models.ForeignKey(
        Organisation, on_delete=models.PROTECT, related_name="orders"
    )
    reference = models.CharField(max_length=32, unique=True, db_index=True)
    title = models.CharField(max_length=200)

    scope = models.TextField(
        help_text="What is being built. Charter 05 §I — fixed scope, in writing."
    )
    exclusions = models.TextField(
        blank=True,
        help_text=(
            "What is NOT included. Charter 05 §I requires exclusions stated in "
            "writing BEFORE work begins. Showing them to the client is the point."
        ),
    )

    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.SCOPING
    )

    service = models.ForeignKey(
        "portal.Service",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="orders",
        help_text=(
            "What was sold. Optional: orders predate the catalogue, and some "
            "work does not fit an offering. PROTECT rather than SET_NULL — "
            "deleting a service would erase what was sold, so retire it instead."
        ),
    )

    # Charter 05 §I — "a named point of contact".
    contact = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="orders_as_contact",
        limit_choices_to={"is_staff": True},
    )

    class Kind(models.TextChoices):
        """
        What SHAPE of work this is, which is not the same as its status.

        ══════════════════════════════════════════════════════════════════════
        A PROJECT ENDS. A RETAINER DOES NOT, AND THE DIFFERENCE IS NOT COSMETIC.

        Every expectation attached to an order assumes a project: a fixed
        scope, a delivery date, gates that get met, a thing that finishes. A
        hosting arrangement or a monthly retainer has none of those and never
        will, so measuring one against them produces an order that is
        permanently late and a delivery board that is permanently wrong.
        ══════════════════════════════════════════════════════════════════════
        """

        PROJECT = "project", "Project"
        RETAINER = "retainer", "Retainer"
        # Ongoing changes to something already built — the commonest shape of
        # work with a client we delivered to a year ago.
        UPDATES = "updates", "Ongoing updates"
        HOSTING = "hosting", "Hosting and upkeep"

    kind = models.CharField(
        max_length=16, choices=Kind.choices, default=Kind.PROJECT, db_index=True
    )

    started_on = models.DateField(null=True, blank=True)
    completed_on = models.DateField(
        null=True,
        blank=True,
        help_text=(
            "When the work actually finished. Set on past work recorded after "
            "the fact; a project delivered through this system gets it when its "
            "status moves to delivered."
        ),
    )

    # ── recorded after the fact ─────────────────────────────────────────────
    #
    # ══════════════════════════════════════════════════════════════════════
    # THIS FLAG EXISTS TO STOP TEN HISTORICAL ORDERS SETTING OFF TEN ALARMS.
    #
    # Charter 05 §III promises a written progress note every week, and the
    # operations queue counts active orders that have not had one. Backfilling
    # work Genmars delivered in 2025 would light that counter up for
    # engagements that finished a year ago — notes that were never going to be
    # written, for a promise that was not in force at the time.
    #
    # It also changes what the CLIENT is told. An order recorded
    # retrospectively must not show "you will get a written update every week
    # this engagement is active" against work that is already done.
    # ══════════════════════════════════════════════════════════════════════
    recorded_retrospectively = models.BooleanField(
        default=False,
        help_text="True for work that happened before it was entered here.",
    )

    # ── something the client should notice ──────────────────────────────────
    #
    # ══════════════════════════════════════════════════════════════════════
    # STAMPED EXPLICITLY, NEVER INFERRED FROM `updated_at`.
    #
    # `updated_at` moves when anything on this row is touched, including things
    # no client has any reason to care about — a service reassigned, a target
    # date corrected by a day, a status nudged from scoping to active. Using it
    # would put a "something changed" badge in front of a client several times
    # a week for changes that mean nothing to them, and a badge that is usually
    # noise is a badge people stop seeing.
    #
    # So it is set by the handful of services that change what the client was
    # TOLD: a progress note published, a statement of work issued or signed, an
    # invoice raised, and — the one that matters most — the SCOPE being edited.
    # Charter 05 §I fixes the scope in writing, and a client whose scope
    # changed without noticing has had that promise quietly broken.
    # ══════════════════════════════════════════════════════════════════════
    client_notice_at = models.DateTimeField(null=True, blank=True, db_index=True)
    client_notice_reason = models.CharField(
        max_length=120,
        blank=True,
        help_text="What changed, in the client's words. Shown beside the marker.",
    )
    target_date = models.DateField(
        null=True,
        blank=True,
        help_text=(
            "Charter 05 §II — a change request may move this. The revised date "
            "is stated at approval, not discovered later."
        ),
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.reference} — {self.title}"

    @property
    def is_active(self) -> bool:
        return self.status in {self.Status.SCOPING, self.Status.ACTIVE, self.Status.REVIEW}


class ProgressNote(models.Model):
    """
    The weekly note. Charter 05 §III: "every active project, same day each week,
    even when progress is thin."

    IMMUTABLE once published. A progress log that can be quietly rewritten is
    not a record, and the client is entitled to rely on what they were told.
    Corrections are a new note.
    """

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="notes")
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="progress_notes",
        limit_choices_to={"is_staff": True},
    )

    body = models.TextField()
    week_of = models.DateField(help_text="The Monday of the week this note covers.")

    published_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-week_of", "-created_at"]
        unique_together = [("order", "week_of")]

    def __str__(self) -> str:
        return f"{self.order.reference} — week of {self.week_of}"

    @property
    def is_published(self) -> bool:
        return self.published_at is not None

    def save(self, *args, **kwargs):
        # Once published, only publishing metadata may change.
        if self.pk and self.is_published:
            existing = ProgressNote.objects.filter(pk=self.pk).values("body").first()
            if existing and existing["body"] != self.body:
                raise ValueError(
                    "A published progress note cannot be edited. Write a new note."
                )
        super().save(*args, **kwargs)


class Milestone(models.Model):
    """
    Charter 05 §VI. Amounts in KES; the charter sets KES for local clients with
    a USD reference for regional work, which is a display concern, not storage.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        INVOICED = "invoiced", "Invoiced"
        PAID = "paid", "Paid"

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="milestones")
    name = models.CharField(max_length=200)
    amount_kes = models.DecimalField(max_digits=12, decimal_places=2)
    due_on = models.DateField(null=True, blank=True)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.PENDING
    )
    position = models.PositiveIntegerField(default=0)

    paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["position", "due_on"]

    def __str__(self) -> str:
        return f"{self.order.reference} — {self.name}"

    def mark_paid(self) -> None:
        self.status = self.Status.PAID
        self.paid_at = timezone.now()
        self.save(update_fields=["status", "paid_at"])


class Enquiry(models.Model):
    """
    What onboarding produces. NOT an order.

    A prospect describes what they need; the commercial partners qualify it
    (Playbook §3) and the founder confirms capacity (Charter 02 §I). Only then
    does an Order exist.
    """

    class Status(models.TextChoices):
        NEW = "new", "New"
        QUALIFYING = "qualifying", "Qualifying"
        CONVERTED = "converted", "Converted to order"
        DECLINED = "declined", "Declined"

    organisation = models.ForeignKey(
        Organisation, on_delete=models.CASCADE, related_name="enquiries"
    )
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="enquiries"
    )

    # The Playbook's qualification questions, asked of the client for their
    # benefit so the first reply is useful rather than a list of questions back.
    problem = models.TextField("What is happening today that prompted this?")
    monthly_cost = models.CharField(
        "Roughly what does it cost per month?", max_length=200, blank=True
    )
    timeline = models.CharField(max_length=100, blank=True)
    budget_range = models.CharField(max_length=100, blank=True)

    # ---- what they asked for ----
    #
    # Set when the enquiry came from a specific offering on genmars.co.ke
    # rather than the open "describe your problem" route. Both are OPTIONAL and
    # always will be: a prospect who does not know which service they need has
    # a real problem, and a form that forced them to pick one would be asking
    # them to guess at our catalogue before we have spoken.
    #
    # SET_NULL, not CASCADE. Retiring a service must not delete the record of
    # people who asked for it — that history is precisely what tells you
    # whether retiring it was right.
    service = models.ForeignKey(
        "portal.Service",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="enquiries",
    )
    # The tier LABEL as it was shown on the website, e.g. "Business Setup".
    #
    # Deliberately text and not a foreign key. Tiers and their prices live in
    # the website's catalogue, and modelling them here would put the same
    # pricing in two systems that must never disagree. What matters at this
    # point is the historical fact of which tier the client clicked, and text
    # records that even after the tier is renamed or repriced.
    tier = models.CharField(max_length=120, blank=True)

    status = models.CharField(max_length=16, choices=Status.choices, default=Status.NEW)
    created_at = models.DateTimeField(auto_now_add=True)

    # ── triage, written by the operations app ────────────────────────────────

    converted_to = models.OneToOneField(
        "portal.Order",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="from_enquiry",
        help_text=(
            "The order this enquiry became. Status CONVERTED without this was a "
            "dead end: it recorded that a decision happened and lost what it "
            "decided, so nobody could get from an enquiry to the work it turned "
            "into. SET_NULL rather than CASCADE — deleting an order must not "
            "erase the enquiry that produced it, which is the record of why we "
            "took the work on."
        ),
    )

    outcome_note = models.TextField(
        blank=True,
        help_text=(
            "Why this was declined, or anything the conversion needs remembered. "
            "Charter 04 §III — a decline we cannot explain six months later is a "
            "decision we did not really make."
        ),
    )

    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="enquiries_decided",
        limit_choices_to={"is_staff": True},
        help_text="Charter 01 §V — nothing ships without a named owner.",
    )
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name_plural = "enquiries"

    def __str__(self) -> str:
        return f"{self.organisation.name} — {self.get_status_display()}"


# ─────────────────────────────────────────────────────────────────────────────
# Engineering delivery
# ─────────────────────────────────────────────────────────────────────────────


class DeliveryGate(models.Model):
    """
    One of the six definition-of-done gates, for one order.

    ── WHY THIS MODEL EXISTS ───────────────────────────────────────────────────
    Charter 03 §II lists six conditions and says plainly that partially done is
    not done. gen-website publishes all six to anyone who visits /approach/.
    Nothing anywhere recorded which of them were met for any actual piece of
    work, so a promise the company makes in public was unauditable in private —
    the exact shape of claim Charter 04 §IV exists to prevent.

    ── WHY THE TEXT IS COPIED, NOT REFERENCED ─────────────────────────────────
    `label` stores the wording as it stood when the gate was created, rather
    than looking it up from a list at render time. If the definition of done is
    ever reworded, orders already delivered must keep showing the standard they
    were actually held to. A gate that silently re-labels itself is a record
    that changes its own history.

    The canonical list lives in `Gate.CHOICES` below and in gen-website's
    `definitionOfDone`. Those two must be changed in the same sitting — see
    gen-website/docs/PORTAL-INTEGRATION.md §3 on duplication.
    """

    class Gate(models.TextChoices):
        REALISTIC_DATA = "realistic_data", "It works against realistic data, not the happy path only"
        TESTS_IN_CI = "tests_in_ci", "Automated tests cover the critical paths, and they pass in CI"
        DEPLOYED = "deployed", "It is deployed to the target environment, not just to a branch"
        MONITORED = "monitored", "Errors surface in monitoring rather than in a client phone call"
        RUNBOOK = "runbook", "The deploy and rollback procedure is written down"
        CLIENT_CAN_USE = "client_can_use", "The client can perform the task the feature was built for, unaided"

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="gates")
    gate = models.CharField(max_length=32, choices=Gate.choices)
    label = models.CharField(
        max_length=200,
        help_text="The wording at the time this gate was created. Never rewritten.",
    )

    met_at = models.DateTimeField(null=True, blank=True)
    met_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="gates_met",
        limit_choices_to={"is_staff": True},
        help_text="Charter 01 §V — nothing ships without a named owner.",
    )
    note = models.TextField(
        blank=True,
        help_text=(
            "How this was satisfied. A tick with no evidence is an opinion; the "
            "note is what makes it a record."
        ),
    )

    position = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["position"]
        unique_together = [("order", "gate")]

    def __str__(self) -> str:
        return f"{self.order.reference} — {self.get_gate_display()}"

    @property
    def is_met(self) -> bool:
        return self.met_at is not None


class Blocker(models.Model):
    """
    Something stopping delivery, and who it is waiting on.

    NOT client-visible. A blocker is an internal working note and often names
    the client as the party being waited on; publishing that unedited to the
    client's own dashboard would turn a working tool into an accusation. What
    the client sees is the weekly progress note, which is written for them.
    """

    class WaitingOn(models.TextChoices):
        US = "us", "Us"
        CLIENT = "client", "The client"
        THIRD_PARTY = "third_party", "A third party"

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="blockers")
    summary = models.CharField(max_length=200)
    detail = models.TextField(blank=True)
    waiting_on = models.CharField(
        max_length=16, choices=WaitingOn.choices, default=WaitingOn.US
    )

    raised_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="blockers_raised",
        limit_choices_to={"is_staff": True},
    )
    raised_at = models.DateTimeField(auto_now_add=True)
    cleared_at = models.DateTimeField(null=True, blank=True)
    resolution = models.TextField(blank=True)

    class Meta:
        ordering = ["cleared_at", "-raised_at"]

    def __str__(self) -> str:
        return f"{self.order.reference} — {self.summary}"

    @property
    def is_open(self) -> bool:
        return self.cleared_at is None

    @property
    def age_days(self) -> int:
        end = self.cleared_at or timezone.now()
        return (end - self.raised_at).days


# ─────────────────────────────────────────────────────────────────────────────
# Services and contracts
# ─────────────────────────────────────────────────────────────────────────────


class Service(models.Model):
    """
    A reusable offering, with the wording we normally use for it.

    ── WHAT THIS IS FOR ────────────────────────────────────────────────────────
    Every order needs a scope and exclusions in writing before work begins
    (Charter 05 §I). Written from scratch each time, the exclusions are the part
    that gets thinned out under time pressure — and exclusions are precisely the
    part that matters in month three.

    So a service carries the wording we have already decided on, and converting
    an enquiry pre-fills from it. It is a STARTING POINT, not a template that
    ships as-is: the scope on the order is edited freely afterwards and the
    contract snapshots the edited version, not this.

    The four offers on genmars.co.ke (paid discovery, custom build, payments and
    reconciliation, maintenance retainer) are the obvious first four. They are
    NOT seeded from company.ts — that file holds marketing copy written to be
    read by a prospect, and a scope clause has a different job.
    """

    name = models.CharField(max_length=120, unique=True)
    slug = models.SlugField(max_length=120, unique=True)
    summary = models.CharField(
        max_length=300,
        help_text="One line, for the picker. What this is, not why it is good.",
    )

    default_scope = models.TextField(
        blank=True, help_text="Pre-fills an order's scope. Edited per client."
    )
    default_exclusions = models.TextField(
        blank=True,
        help_text=(
            "Pre-fills an order's exclusions. The reason this model exists — "
            "exclusions written from scratch under time pressure come out thin."
        ),
    )
    default_deliverables = models.TextField(
        blank=True,
        help_text="One per line. What the client actually receives.",
    )

    # "per month", "one-time", "per session". Carried from the website because
    # a tier card showing KES 10,000 for something billed monthly misstates the
    # price — Charter 04 §IV, and the kind of error a client only finds on the
    # second invoice.
    price_unit = models.CharField(
        max_length=40,
        blank=True,
        help_text='How the tier prices are charged: "per month", "one-time".',
    )

    is_active = models.BooleanField(
        default=True,
        help_text=(
            "Retired services stay in the table. Orders and contracts reference "
            "them, and deleting one would rewrite the record of what was sold."
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name

    @property
    def deliverable_list(self) -> list[str]:
        return [line.strip() for line in self.default_deliverables.splitlines() if line.strip()]


class Contract(models.Model):
    """
    A statement of work: what was agreed, frozen at the moment it was issued.

    ══════════════════════════════════════════════════════════════════════════
    EVERY FIELD HERE IS A SNAPSHOT, AND THAT IS THE ENTIRE POINT.

    The obvious implementation is a view that renders the order — scope,
    exclusions, milestones — as a document. It is wrong, and quietly so: edit
    the order's scope afterwards and the "contract" the client signed now says
    something they never agreed to. A document that changes underneath the
    person who signed it is not a contract, it is a web page.

    So issuing COPIES the wording and the money as they stood, and nothing
    after that touches them. Charter 05 §I promises a fixed scope and a fixed
    price with exclusions stated in writing BEFORE work begins; this is the
    object that makes "fixed" true rather than aspirational.
    ══════════════════════════════════════════════════════════════════════════

    Changing the deal means issuing a NEW version, which supersedes the old one.
    Both stay. What was agreed in March is still readable in September, which is
    the only reason anybody keeps contracts.
    """

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ISSUED = "issued", "Issued"
        SIGNED = "signed", "Signed"
        SUPERSEDED = "superseded", "Superseded"
        VOID = "void", "Void"

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="contracts")
    version = models.PositiveIntegerField(default=1)

    # ---- the snapshot ----
    title = models.CharField(max_length=200)
    scope = models.TextField()
    exclusions = models.TextField(blank=True)
    deliverables = models.TextField(
        blank=True, help_text="One per line, as they stood when issued."
    )
    total_kes = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        help_text=(
            "Summed from the order's milestones at issue. A Decimal, never a "
            "float — money through a float is money you cannot reconcile."
        ),
    )
    payment_terms = models.TextField(
        blank=True, help_text="Milestone names and amounts, as they stood."
    )
    target_date = models.DateField(null=True, blank=True)

    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)

    issued_at = models.DateTimeField(null=True, blank=True)
    issued_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="contracts_issued",
        limit_choices_to={"is_staff": True},
    )

    # ---- signature ----
    #
    # RECORDED, NOT COLLECTED. Genmars does not run an e-signature product and
    # must not imply one: this says a signature happened somewhere else — an
    # email, a PDF, a meeting — and who recorded that. Charter 04 §IV forbids
    # claiming a capability we do not have, and "signed in the portal" would be
    # exactly that claim.
    signed_on = models.DateField(null=True, blank=True)
    signed_by_name = models.CharField(
        max_length=200, blank=True, help_text="The person at the client who signed."
    )
    signature_note = models.TextField(
        blank=True,
        help_text="How it was signed and where the evidence is. Not a signature.",
    )
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="contracts_recorded",
        limit_choices_to={"is_staff": True},
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-version"]
        unique_together = [("order", "version")]

    def __str__(self) -> str:
        return f"{self.order.reference} SOW v{self.version} ({self.get_status_display()})"

    @property
    def reference(self) -> str:
        return f"{self.order.reference}-SOW-{self.version:02d}"

    @property
    def is_live(self) -> bool:
        """Issued or signed — the version currently in force."""
        return self.status in {self.Status.ISSUED, self.Status.SIGNED}

    @property
    def deliverable_list(self) -> list[str]:
        return [line.strip() for line in self.deliverables.splitlines() if line.strip()]


class Invoice(models.Model):
    """
    A request for payment, frozen at the moment it was issued.

    ══════════════════════════════════════════════════════════════════════════
    THE SAME SNAPSHOT RULE AS Contract, AND FOR A SHARPER REASON.

    An invoice that recalculates from the milestone is not an invoice. Edit the
    milestone amount in October and every invoice sent in June silently changes
    what it asked for — including ones already paid, which now disagree with the
    client's bank statement. There is no way to explain that to a client, and no
    way to audit it afterwards.

    So issuing COPIES the amount and the description as they stood. Nothing
    after that touches them. Correcting an invoice means VOIDING it and issuing
    a new one, which is what a paper trail is.
    ══════════════════════════════════════════════════════════════════════════

    ── HOW PAYMENT REACHES THIS ROW ────────────────────────────────────────────

    Two ways in, and neither of them lets this model mark itself paid.

      · M-Pesa STK push. The client taps Pay, Safaricom prompts their phone,
        and `record_mpesa_result` writes the outcome when the callback lands.
        The push is real — Charter 04 §IV forbids a "Pay now" button that only
        marks a row, because the client would believe they had paid.

      · Recorded by hand, via PaymentRecord. Money that arrived by bank
        transfer or at a till, written down by someone here with the reference
        off the statement. Genmars does not move that money; it records that
        it moved.

    In both cases the amount is checked before anything is marked paid. See
    PaymentRecord for why payment is a LIST rather than a single reference.
    """

    class Status(models.TextChoices):
        ISSUED = "issued", "Issued"
        PAID = "paid", "Paid"
        VOID = "void", "Void"

    number = models.CharField(
        max_length=32,
        unique=True,
        db_index=True,
        help_text="GM-INV-2026-0001. Sequential, never reused — including voids.",
    )

    # ── WHO IS BEING BILLED ─────────────────────────────────────────────────
    # The organisation, always. The order is optional, because not every
    # invoice comes from a project: a past client asking for a day's work, a
    # renewal, a licence. Billing those through a fake order would put rows in
    # the delivery pipeline for work that has no pipeline.
    #
    # An invoice with no organisation is an invoice addressed to nobody, so
    # that is the field that cannot be null. When an order IS attached, its
    # organisation must be this one — enforced by a constraint below, because
    # an invoice filed against the wrong client is visible to the wrong client.
    # ── who it was billed to, as it read on the day ─────────────────────────
    #
    # A COPY of the organisation's name, not a lookup through the FK.
    #
    # This model's docstring already commits to the snapshot rule for amounts,
    # and the billed-to line needs it for exactly the same reason. Reading
    # `organisation.name` live meant that renaming a client — a correction, a
    # rebrand, a change of legal entity — silently rewrote the "To:" line on
    # every invoice already issued, sent and paid. The client's PDF and ours
    # would then disagree about who was billed, which is the one thing an
    # invoice number exists to make impossible.
    #
    # Blank on rows issued before this field existed; the serializer falls back
    # to the live name for those, which is what they were already showing.
    billed_to_name = models.CharField(max_length=200, blank=True)

    organisation = models.ForeignKey(
        Organisation,
        on_delete=models.PROTECT,
        related_name="invoices",
        help_text="The client this is addressed to.",
    )
    order = models.ForeignKey(
        Order,
        on_delete=models.PROTECT,
        related_name="invoices",
        null=True,
        blank=True,
        help_text="The project this bills, when it bills one.",
    )
    # Usually a milestone, because that is how Charter 05 §VI structures money.
    # Nullable for the occasional thing that is genuinely not one — a change
    # request billed on its own, or a retainer month.
    milestone = models.ForeignKey(
        Milestone,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="invoices",
    )

    # ---- the snapshot ----
    description = models.CharField(
        max_length=300, help_text="What this bills, as it stood at issue."
    )
    amount_kes = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text=(
            "A Decimal, never a float. Money through a float is money you "
            "cannot reconcile, and this is the number on a document someone pays."
        ),
    )

    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.ISSUED
    )

    issued_on = models.DateField()
    due_on = models.DateField(
        null=True,
        blank=True,
        help_text="Stated so 'overdue' is a fact rather than a feeling.",
    )
    issued_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="invoices_issued",
        limit_choices_to={"is_staff": True},
    )

    # ---- payment, recorded ----
    #
    # These two are a SUMMARY of the PaymentRecord rows, kept because an
    # invoice that has been paid should be able to say when and against what
    # without a join. They are written by services.record_payment and by the
    # M-Pesa callback; nothing else should touch them. paid_on is the date of
    # the payment that settled the balance, and payment_reference lists the
    # references that made it up.
    paid_on = models.DateField(null=True, blank=True)
    payment_reference = models.CharField(
        max_length=120,
        blank=True,
        help_text=(
            "The reference from the statement — an M-Pesa code, a bank "
            "reference. What lets this row be checked against the account."
        ),
    )
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="invoices_recorded",
        limit_choices_to={"is_staff": True},
    )

    # ---- void ----
    void_reason = models.TextField(
        blank=True,
        help_text="Why. A voided invoice with no reason is an unanswered question.",
    )
    voided_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-issued_on", "-number"]
        constraints = [
            # A milestone belongs to an order; an invoice that bills a
            # milestone but names no order has lost the thread between them.
            models.CheckConstraint(
                condition=models.Q(milestone__isnull=True)
                | models.Q(order__isnull=False),
                name="invoice_milestone_requires_order",
            ),
        ]

    def __str__(self) -> str:
        subject = self.order.reference if self.order_id else self.organisation.name
        return f"{self.number} — {subject} ({self.get_status_display()})"

    @property
    def is_direct(self) -> bool:
        """Billed to a client rather than against a project."""
        return self.order_id is None

    @property
    def amount_paid(self) -> Decimal:
        """
        What has actually been recorded against this, summed.

        Voided invoices still report their payments rather than zero. Hiding
        them would make a void look like a way to erase money that arrived,
        which is exactly what void_invoice refuses to allow.
        """
        total = self.payments.aggregate(total=models.Sum("amount_kes"))["total"]
        return total or Decimal("0.00")

    @property
    def balance(self) -> Decimal:
        """What is still owed. Never negative — see settle()."""
        return self.amount_kes - self.amount_paid

    @property
    def is_outstanding(self) -> bool:
        return self.status == self.Status.ISSUED

    def is_overdue(self, today: date | None = None) -> bool:
        """
        Unpaid and past its due date.

        A method rather than a property because it takes `today`: "overdue" is
        a claim about a moment, and a test that cannot choose the moment is a
        test that passes in the morning and fails at night.
        """
        if not self.is_outstanding or not self.due_on:
            return False
        return self.due_on < (today or timezone.localdate())


class MpesaPayment(models.Model):
    """
    One STK push attempt, and what became of it.

    ── WHY THIS IS A ROW AND NOT A FIELD ON Invoice ───────────────────────────

    A customer pushes the button, the prompt times out, they push it again. A
    single "mpesa_receipt" column would lose the first attempt, which is
    exactly the attempt somebody rings up about ("it took the money and said it
    failed"). Every push is recorded, successful or not, and the invoice is
    marked paid by at most one of them.

    ── THE RAW CALLBACK IS KEPT ───────────────────────────────────────────────

    Reconciling a disputed payment months later means answering "what did
    Safaricom actually tell us", and a parsed subset cannot answer that. It is
    small, it is written once, and it is the difference between a five-minute
    answer and an argument.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Prompt sent"
        SUCCESS = "success", "Paid"
        FAILED = "failed", "Failed or cancelled"

    invoice = models.ForeignKey(
        "portal.Invoice", on_delete=models.PROTECT, related_name="mpesa_payments"
    )

    # Daraja's handle for this push. UNIQUE, and it is what makes the callback
    # idempotent: Safaricom retries, and a retry must not pay an invoice twice.
    checkout_request_id = models.CharField(max_length=64, unique=True, db_index=True)
    merchant_request_id = models.CharField(max_length=64, blank=True)

    # The number prompted. Stored because "which phone did we ask" is the first
    # question when a customer says they never got a prompt.
    phone = models.CharField(max_length=16)
    # What we ASKED for, in whole shillings. Compared against what the callback
    # says arrived — see services.record_mpesa_result.
    amount = models.PositiveIntegerField()

    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.PENDING
    )
    result_code = models.CharField(max_length=8, blank=True)
    result_desc = models.TextField(blank=True)
    # The M-Pesa code the customer sees. This is what they will quote.
    receipt = models.CharField(max_length=32, blank=True)

    raw_callback = models.JSONField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.invoice.number} via M-Pesa ({self.get_status_display()})"


class PaymentRecord(models.Model):
    """
    One payment that arrived, with the reference that proves it.

    ══════════════════════════════════════════════════════════════════════════
    WHY PAYMENT IS A LIST AND NOT A FIELD.

    Invoice used to carry one `payment_reference`, which quietly assumed every
    invoice is settled by exactly one transaction. In Kenya that assumption is
    wrong often enough to matter: M-Pesa has a per-transaction ceiling, so a
    large invoice is routinely paid as three or four transfers, each with its
    own code. Under the old shape the second code overwrote the first, and the
    invoice claimed to have been settled by a payment that covered a fraction
    of it.

    So payments accumulate. The invoice is marked paid when they ADD UP to the
    full amount, and not one shilling before.
    ══════════════════════════════════════════════════════════════════════════

    ── THE REFERENCE IS THE POINT ──────────────────────────────────────────────

    An M-Pesa confirmation code is unique across the whole network, so the same
    code appearing twice means somebody recorded the same payment twice — most
    likely against two different invoices, which shows the company as having
    been paid money it was never paid. The unique constraint below makes that
    impossible rather than merely discouraged.

    Blank references are exempted from that constraint, because cash does not
    always come with one. That is a deliberate hole and a small one: cash is
    the case where a human already had to be in the room.
    """

    class Method(models.TextChoices):
        MPESA = "mpesa", "M-Pesa"
        BANK = "bank", "Bank transfer"
        CASH = "cash", "Cash"
        OTHER = "other", "Other"

    invoice = models.ForeignKey(
        Invoice, on_delete=models.PROTECT, related_name="payments"
    )
    method = models.CharField(max_length=16, choices=Method.choices)
    reference = models.CharField(
        max_length=64,
        blank=True,
        help_text="The M-Pesa code or bank reference, exactly as on the statement.",
    )
    amount_kes = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text="How much this one payment was. Decimal, never a float.",
    )
    paid_on = models.DateField()
    note = models.CharField(
        max_length=200,
        blank=True,
        help_text="Anything needed to find this on a statement later.",
    )

    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="payments_recorded",
        limit_choices_to={"is_staff": True},
    )
    # Set when the row came from a Daraja callback rather than a person, so
    # "who recorded this" has an answer that is not a misleading blank.
    mpesa_payment = models.OneToOneField(
        "portal.MpesaPayment",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="record",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["paid_on", "id"]
        constraints = [
            models.UniqueConstraint(
                models.functions.Upper("reference"),
                "method",
                condition=~models.Q(reference=""),
                name="unique_payment_reference_per_method",
            ),
            models.CheckConstraint(
                condition=models.Q(amount_kes__gt=0),
                name="payment_amount_positive",
            ),
        ]

    def __str__(self) -> str:
        label = self.reference or self.get_method_display()
        return f"KES {self.amount_kes} — {label}"


class Notification(models.Model):
    """
    Something happened that a specific person should know about.

    ══════════════════════════════════════════════════════════════════════════
    ONE ROW PER PERSON, NOT ONE PER EVENT.

    An invoice issued to a client with four people on the account creates four
    notifications. That is deliberate: "read" is a fact about a person, not
    about an event, and a shared row means one colleague opening it marks it
    read for everyone — which is how a bill goes unnoticed by the person who
    was actually going to pay it.

    The duplication is cheap. The alternative is a join table that stores the
    same thing with more moving parts.
    ══════════════════════════════════════════════════════════════════════════

    ── THIS IS NOT A MESSAGE TO THE CLIENT ─────────────────────────────────────

    A notification is a pointer at something already true and already visible:
    an invoice that exists, an order that moved. It is never the only place a
    fact lives, and it is never how a client is told something that matters —
    that is email, or a person. If the notification is lost, nothing is lost.

    So nothing here should read as a promise, and nothing should carry a detail
    that is not already on the page it links to. Charter 04 §IV applies to this
    text exactly as it applies to the website.
    """

    class Audience(models.TextChoices):
        # Which surface it belongs on. A staff notification must never be
        # queryable from the client API, and the client API filters on this.
        CLIENT = "client", "Client"
        STAFF = "staff", "Staff"

    class Kind(models.TextChoices):
        INVOICE_ISSUED = "invoice_issued", "Invoice issued"
        INVOICE_PAID = "invoice_paid", "Invoice paid"
        PAYMENT_RECORDED = "payment_recorded", "Payment recorded"
        INVOICE_VOIDED = "invoice_voided", "Invoice voided"
        ORDER_UPDATE = "order_update", "Order update"
        ENQUIRY_RECEIVED = "enquiry_received", "Enquiry received"
        INCIDENT_RAISED = "incident_raised", "Incident raised"
        OFFER_SENT = "offer_sent", "Offer received"
        TASK_ASSIGNED = "task_assigned", "Task assigned to you"
        SUPPORT_REPLY = "support_reply", "Reply on your support request"
        SUPPORT_RAISED = "support_raised", "New support request"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    audience = models.CharField(max_length=16, choices=Audience.choices)
    kind = models.CharField(max_length=32, choices=Kind.choices)

    title = models.CharField(max_length=200)
    body = models.CharField(max_length=400, blank=True)
    # Relative, and always within the app that owns the audience. Absolute URLs
    # are refused by the serializer: a notification is not a place to put a
    # link somebody clicked without looking.
    url = models.CharField(max_length=300, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            # The unread count runs on every page load of both apps.
            models.Index(
                fields=["user", "audience", "read_at"],
                name="notification_inbox_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.title} -> {self.user.email}"

    @property
    def is_read(self) -> bool:
        return self.read_at is not None


class ServiceTier(models.Model):
    """
    One of the three sizes a service is sold in.

    ══════════════════════════════════════════════════════════════════════════
    THE WEBSITE IS THE PRICE LIST. THIS IS A COPY OF IT.

    genmars.co.ke/services publishes these prices to the public, and that page
    is authoritative. These rows exist so a signed-in client can pick a tier
    inside the portal without being sent back out to the website to read the
    number and come back — not so the portal can have prices of its own.

    Two price lists is how a client is quoted one number and billed another, so
    when a price changes on the website `seed_services --force` must be re-run.
    The seed command carries the same data and says the same thing.
    ══════════════════════════════════════════════════════════════════════════

    ── WHY THE PRICE IS A NUMBER AND NOT THE STRING FROM THE WEBSITE ───────────

    The website stores "KES 25,000" because it renders it. Here it has to be
    compared: picking a tier answers the budget question on the order form, and
    that needs arithmetic, not a string. Decimal, like every other money field
    in this file.

    `is_from` carries what the website calls `open` — the top tier is a floor,
    not a price, and a card that shows KES 150,000 without saying "from" is a
    quote we have not given.
    """

    service = models.ForeignKey(
        Service, on_delete=models.CASCADE, related_name="tiers"
    )
    # Not globally unique: "enterprise" is a tier of several services, and
    # "basic" of two. Unique WITHIN a service, which is what identifies one.
    slug = models.SlugField(max_length=120)
    name = models.CharField(max_length=120)

    price_kes = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Null where the tier is quoted individually rather than listed.",
    )
    is_from = models.BooleanField(
        default=False,
        help_text='Shown as "from KES X" — a floor, not a quote.',
    )

    # ── WHAT THE WEBSITE CURRENTLY SAYS ─────────────────────────────────────
    #
    # price_kes is the working price and operations can edit it. This one is
    # what genmars.co.ke actually publishes, written only by seed_services from
    # company.ts.
    #
    # They exist separately because the website is a static export on a
    # different deploy cycle: a price changed here is live in the portal
    # immediately and still wrong on the public page until someone ships the
    # site. Storing both makes that gap VISIBLE — operations shows "the website
    # still says X" — instead of leaving a client quoted one number on the page
    # and another in the portal, which is the failure this pair exists to
    # prevent.
    published_price_kes = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="What genmars.co.ke publishes. Set by seed_services, never by hand.",
    )

    lead = models.CharField(
        max_length=200, help_text="One line on who this size is for."
    )
    includes = models.TextField(
        help_text="One item per line, in the order the website lists them."
    )

    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["service", "position", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["service", "slug"], name="unique_tier_slug_per_service"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.service.name} — {self.name}"

    @property
    def included(self) -> list[str]:
        return [line.strip() for line in self.includes.splitlines() if line.strip()]

    @property
    def differs_from_website(self) -> bool:
        """
        The working price and the published price disagree.

        Not an error — it is the ordinary state between changing a price and
        shipping the site. It has to be visible, because the window is exactly
        when a client can be quoted two different numbers.
        """
        if self.published_price_kes is None:
            return False
        return self.price_kes != self.published_price_kes


class Incident(models.Model):
    """
    Something broke, and what we did about it.

    ══════════════════════════════════════════════════════════════════════════
    THIS EXISTS BECAUSE THE WEBSITE ALREADY PROMISES IT.

    genmars.co.ke/approach publishes, to anyone who reads it:

        "Every SEV-1 produces a written post-mortem: what happened, why, and
         what prevents recurrence. Post-mortems are blameless, and they are
         kept permanently."

    Charter 04 §IV forbids anything untrue on a Genmars surface, and a promise
    with no mechanism behind it is a claim we cannot keep on a day we are busy
    — which is precisely the day it gets made. So the promise is enforced here
    rather than remembered: services.close_incident REFUSES to close a SEV-1
    whose post-mortem is unwritten.
    ══════════════════════════════════════════════════════════════════════════

    ── "BLAMELESS" IS A SCHEMA DECISION, NOT A VALUE STATEMENT ─────────────────

    There is no `responsible_person` field, and there should never be one. A
    column for who caused it turns the record into an accusation and guarantees
    the next incident gets written up carefully rather than honestly. `raised_by`
    and `closed_by` exist because someone has to be accountable for the WRITING;
    they are not the cause.

    ── "KEPT PERMANENTLY" MEANS THERE IS NO DELETE ─────────────────────────────

    Closing is a status, never a removal. An incident that stops being findable
    the moment it stops being urgent is how the same failure happens twice.
    """

    class Severity(models.TextChoices):
        # Wording copied from the published table, deliberately. If these ever
        # disagree with the website, the website is right and this is a bug.
        SEV1 = "sev1", "SEV-1 — system down, or data at risk"
        SEV2 = "sev2", "SEV-2 — major function broken, workaround exists"
        SEV3 = "sev3", "SEV-3 — minor defect, cosmetic issue"

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        # Bleeding stopped, root cause not yet fixed. A real and common state,
        # and collapsing it into "closed" is how a workaround becomes permanent.
        MITIGATED = "mitigated", "Mitigated"
        CLOSED = "closed", "Closed"

    reference = models.CharField(max_length=32, unique=True, db_index=True)
    title = models.CharField(
        max_length=200, help_text="What broke, in the words someone would search for."
    )
    severity = models.CharField(max_length=8, choices=Severity.choices)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.OPEN
    )

    # ---- the timeline ----
    #
    # `started_at` is when the failure BEGAN, not when we noticed. The gap
    # between the two is the most useful number in the whole record: it is how
    # long a thing was broken with nobody looking, and it is the number that
    # says whether monitoring works.
    started_at = models.DateTimeField(
        help_text="When it actually began — not when we noticed."
    )
    detected_at = models.DateTimeField(help_text="When a human first knew.")
    mitigated_at = models.DateTimeField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    # ---- impact ----
    summary = models.TextField(help_text="What was happening, plainly.")
    client_impact = models.TextField(
        blank=True,
        help_text=(
            "What clients could and could not do. Blank means none — say so "
            "rather than leaving it to be assumed."
        ),
    )
    affected = models.ManyToManyField(
        Organisation,
        blank=True,
        related_name="incidents",
        help_text="Named clients, where the impact was specific rather than general.",
    )

    # ---- the post-mortem ----
    #
    # Three fields because the published promise names three things. Splitting
    # them matters: a single free-text box gets filled with what happened and
    # stops, and "what prevents recurrence" is the only part that changes the
    # future.
    what_happened = models.TextField(blank=True, help_text="The sequence, in order.")
    why = models.TextField(
        blank=True,
        help_text="The cause. Not the trigger — the reason the trigger mattered.",
    )
    prevention = models.TextField(
        blank=True,
        help_text="What now makes this not happen again, and where that lives.",
    )

    raised_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="incidents_raised",
        limit_choices_to={"is_staff": True},
    )
    closed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="incidents_closed",
        limit_choices_to={"is_staff": True},
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-started_at", "-id"]

    def __str__(self) -> str:
        return f"{self.reference} — {self.title}"

    @property
    def is_open(self) -> bool:
        return self.status != self.Status.CLOSED

    @property
    def needs_post_mortem(self) -> bool:
        """A SEV-1 with any of the three parts unwritten."""
        return self.severity == self.Severity.SEV1 and not self.has_post_mortem

    @property
    def has_post_mortem(self) -> bool:
        return all(
            field.strip() for field in (self.what_happened, self.why, self.prevention)
        )

    def undetected_for(self) -> timedelta | None:
        """
        How long it ran before anybody knew.

        The number that says whether monitoring works, which is why it is
        computed rather than typed: a field would be filled in optimistically.
        """
        if not self.started_at or not self.detected_at:
            return None
        return self.detected_at - self.started_at


class ActivityLog(models.Model):
    """
    Who did what, in order.

    ══════════════════════════════════════════════════════════════════════════
    APPEND-ONLY. THERE IS NO EDIT AND NO DELETE.

    A log somebody can amend is not a log, it is a draft. Nothing in this
    codebase updates a row here after writing it, and nothing should: if an
    entry is wrong, the correction is another entry saying so.

    That also means this is not the place for state. The invoice knows whether
    it is paid; this knows that on Tuesday at 14:02 somebody recorded a payment
    against it. Those are different facts and the second one survives the first
    being changed.
    ══════════════════════════════════════════════════════════════════════════

    ── WHAT MUST NEVER BE WRITTEN HERE ─────────────────────────────────────────

    Verification codes, reset codes, API keys, M-Pesa passkeys, passwords or
    anything derived from them. A log is the place secrets go to live forever
    on disk, be read by everyone with operations access, and end up in a
    backup. `detail` is a free-form JSON field and is therefore the exact hole
    this warning exists for.

    Amounts, references, names and email addresses are fine — they are already
    visible to anyone who can read this table.

    ── WHY A STRING SUBJECT AND NOT A GENERIC FOREIGN KEY ──────────────────────

    A GenericForeignKey would cascade or dangle when the thing it points at
    changes, and the whole value of a log is that it still reads correctly
    after the subject is gone. "GM-INV-2026-0004" stays meaningful when the
    invoice does not. The FKs below are for FILTERING, are nullable, and are
    never what the entry means.
    """

    class Action(models.TextChoices):
        # Money
        INVOICE_ISSUED = "invoice.issued", "Invoice issued"
        INVOICE_PAID = "invoice.paid", "Invoice settled"
        PAYMENT_RECORDED = "payment.recorded", "Payment recorded"
        INVOICE_VOIDED = "invoice.voided", "Invoice voided"
        # Agreements
        CONTRACT_ISSUED = "contract.issued", "Statement of work issued"
        CONTRACT_SIGNED = "contract.signed", "Statement of work signed"
        CONTRACT_VOIDED = "contract.voided", "Statement of work voided"
        # Pipeline
        ENQUIRY_CONVERTED = "enquiry.converted", "Enquiry converted to an order"
        ENQUIRY_DECLINED = "enquiry.declined", "Enquiry declined"
        # Catalogue and commercial
        PRICE_CHANGED = "price.changed", "Tier price changed"
        OFFER_SENT = "offer.sent", "Offer sent to a client"
        OFFER_ACCEPTED = "offer.accepted", "Offer accepted"
        OFFER_WITHDRAWN = "offer.withdrawn", "Offer withdrawn"
        # People
        STAFF_INVITED = "staff.invited", "Staff member invited"
        STAFF_ROLE_CHANGED = "staff.role_changed", "Staff role changed"
        STAFF_DEACTIVATED = "staff.deactivated", "Staff access revoked"
        ACCESS_GRANTED = "access.granted", "Client access granted"
        ACCESS_REVOKED = "access.revoked", "Client access revoked"
        # Work
        TASK_ASSIGNED = "task.assigned", "Task assigned"
        TASK_DONE = "task.done", "Task completed"
        # Reliability
        INCIDENT_RAISED = "incident.raised", "Incident raised"
        INCIDENT_CLOSED = "incident.closed", "Incident closed"

        # Which FIELDS changed, never their values. A paybill or a bank
        # account in the log is not a secret, but it is the record used to
        # investigate a fraudulent change, and a log that stores the new
        # details is one more place they can be read from.
        BILLING_CHANGED = "billing.changed", "Billing details changed"

        # The workroom. A shift is logged for the same reason a payment is:
        # the Shift row is the state and can be corrected, this is the account
        # of what was done and when, and it survives the correction.
        SHIFT_STARTED = "shift.started", "Clocked in"
        SHIFT_ENDED = "shift.ended", "Clocked out"
        DECISION_RECORDED = "decision.recorded", "Decision recorded"
        DECISION_MADE = "decision.made", "Decision made"
        DECISION_SUPERSEDED = "decision.superseded", "Decision superseded"
        DECISION_REVERSED = "decision.reversed", "Decision reversed"

        # The client record. A conversation is logged because the log IS the
        # record; the others are logged because they change what we believe
        # about a client — including who holds their domain.
        CONTACT_LOGGED = "contact.logged", "Conversation recorded"
        FOLLOW_UP_CLEARED = "contact.followed_up", "Follow-up cleared"
        CLIENT_PROFILE_CHANGED = "client.profile_changed", "Client details changed"
        CLIENT_CREATED = "client.created", "Client added"
        CLIENT_RENAMED = "client.renamed", "Client renamed"
        CLIENT_ARCHIVED = "client.archived", "Client archived"
        CLIENT_RESTORED = "client.restored", "Client restored"
        CLIENT_DELETED = "client.deleted", "Client deleted"

        # Asking for, and deciding on, permission to do one thing once.
        ACCESS_REQUESTED = "access.requested", "Permission requested"
        ACCESS_APPROVED = "access.approved", "Permission granted for one act"
        ACCESS_DECLINED = "access.declined", "Permission refused"
        ACCESS_USED = "access.used", "Granted permission used"

        # Not to gate it — every staff account can already read all of this on
        # a screen, and a control that can be defeated by copying is theatre.
        # It is recorded because a bulk export is the shape of the act somebody
        # would want to reconstruct later, and the log is the account of what
        # was done rather than a barrier to doing it.
        REPORT_EXPORTED = "report.exported", "Report exported"

        # Scope, exclusions or the title of an order changing. Its own action
        # because it is the edit a client is entitled to notice — Charter 05 §I
        # fixes the scope in writing, and this is the record that it moved.
        ORDER_UPDATED = "order.updated", "Order scope or title changed"
        HOSTING_RECORDED = "hosting.recorded", "Hosting arrangement recorded"
        HOSTING_CHANGED = "hosting.changed", "Hosting arrangement changed"
        HOSTING_RETIRED = "hosting.retired", "Hosting arrangement retired"

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="activity",
        help_text="Null where the system acted — an M-Pesa callback, a timer.",
    )
    # Kept alongside the FK because SET_NULL loses the name, and "somebody
    # voided this invoice" is a materially worse record than "Asha did".
    actor_label = models.CharField(max_length=200, blank=True)

    action = models.CharField(max_length=40, choices=Action.choices, db_index=True)
    subject = models.CharField(
        max_length=120,
        blank=True,
        help_text="The reference this is about — GM-INV-2026-0004, GM-2026-0001.",
    )
    summary = models.CharField(
        max_length=300, help_text="One line, readable without the detail."
    )
    detail = models.JSONField(
        default=dict,
        blank=True,
        help_text="Structured extras. NEVER a secret — see this model's docstring.",
    )

    # For filtering only. Never what the entry means — see the docstring.
    organisation = models.ForeignKey(
        Organisation,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="activity",
    )

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["-created_at", "action"], name="activity_feed_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.action} {self.subject}"


class Offer(models.Model):
    """
    A price put to a specific client, for a specific piece of work.

    ══════════════════════════════════════════════════════════════════════════
    AN OFFER IS A COMMITMENT, NOT A SUGGESTION.

    Once it is sent, the client can accept it, and the number on it is the
    number we then have to honour. That makes this the same authority as
    pricing and signing — Charter 02 §I — and it is why the amount is FROZEN
    the moment it is sent.

    An offer that recalculated from the catalogue would change under a client
    who was still deciding. They would open it on Friday at a price they had
    read on Tuesday, and neither of us could explain what happened. Same
    snapshot rule as Contract and Invoice, and for the same reason.
    ══════════════════════════════════════════════════════════════════════════

    ── WHY IT EXPIRES ──────────────────────────────────────────────────────────

    Every offer states a date it stops being valid. Not to pressure anybody —
    because an open-ended price is one we are still bound by in a year, after
    costs have moved. `expires_on` is required for exactly that reason.

    ── ACCEPTING DOES NOT START WORK ───────────────────────────────────────────

    It files an enquiry with the offer attached, which the commercial partners
    qualify like any other. Charter 02 §I puts a signed statement of work
    before delivery, and no click by a client should be able to skip it.
    """

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SENT = "sent", "Sent"
        ACCEPTED = "accepted", "Accepted"
        DECLINED = "declined", "Declined"
        WITHDRAWN = "withdrawn", "Withdrawn"
        EXPIRED = "expired", "Expired"

    reference = models.CharField(max_length=32, unique=True, db_index=True)
    organisation = models.ForeignKey(
        Organisation, on_delete=models.PROTECT, related_name="offers"
    )

    # What it is for. The service is a pointer for reporting; the wording below
    # is what the client actually reads and is copied, not looked up.
    service = models.ForeignKey(
        Service,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="offers",
    )
    tier_name = models.CharField(max_length=120, blank=True)

    title = models.CharField(max_length=200, help_text="What is being offered.")
    detail = models.TextField(
        help_text="What it includes, in the client's language. Copied at send."
    )

    # ── the proposal ────────────────────────────────────────────────────────
    #
    # ══════════════════════════════════════════════════════════════════════
    # A QUOTE AND A PROPOSAL ARE THE SAME ROW, FILLED IN TO DIFFERENT DEPTHS.
    #
    # There is no second model, because there is no second thing. A renewal
    # quoted over the phone needs a title, a number and a date; a piece of work
    # somebody has to justify internally needs the reasoning as well. Splitting
    # them would give us two references, two documents and two accept buttons
    # for one commitment — and Charter 03 §I asks whether what is already here
    # can do the job. It can, with more fields.
    #
    # All optional, so a one-line quote stays a one-line quote and the document
    # simply omits the headings nobody filled in.
    # ══════════════════════════════════════════════════════════════════════

    context = models.TextField(
        blank=True,
        help_text=(
            "What we understood the problem to be. The client reads this first "
            "and it is where they find out we were listening — or that we were "
            "not, which is cheaper to discover here than after a deposit."
        ),
    )
    approach = models.TextField(blank=True, help_text="How we would do it.")
    inclusions = models.TextField(blank=True, help_text="What the price covers.")
    exclusions = models.TextField(
        blank=True,
        help_text=(
            "What it does not. Charter 05 §I — exclusions in writing before "
            "work begins, and a quote is before work begins."
        ),
    )
    timeline = models.CharField(
        max_length=300,
        blank=True,
        help_text=(
            "An ESTIMATE of how long, in plain words. Charter 03 §IV forbids "
            "putting a commitment in front of a client that has not been tested "
            "under real conditions, so this is not a deadline and must not be "
            "worded as one."
        ),
    )
    payment_terms = models.TextField(
        blank=True, help_text="Deposit, milestones, what is due when."
    )
    next_step = models.CharField(
        max_length=300,
        blank=True,
        help_text=(
            "What happens if they say yes. Accepting files an enquiry, which we "
            "qualify — it does not start work, and the document should not let "
            "anyone think it does."
        ),
    )

    # Who it was addressed to, as it read on the day. Same rule and the same
    # reason as Invoice.billed_to_name: an offer is a document the client
    # holds a copy of, and renaming the organisation must not change the
    # "To:" line on a quote already sent.
    offered_to_name = models.CharField(max_length=200, blank=True)

    amount_kes = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text="Frozen when sent. Decimal, never a float.",
    )
    # What the catalogue said at the time, so a discount is visible as a
    # decision rather than buried in a number nobody can check.
    list_price_kes = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )

    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.DRAFT
    )
    expires_on = models.DateField(help_text="After this it is not ours to honour.")

    sent_at = models.DateTimeField(null=True, blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    decline_reason = models.TextField(blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="offers_made",
        limit_choices_to={"is_staff": True},
    )
    accepted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="offers_accepted",
    )
    enquiry = models.OneToOneField(
        "portal.Enquiry",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="from_offer",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self) -> str:
        return f"{self.reference} — {self.organisation.name} ({self.get_status_display()})"

    @property
    def discount_kes(self) -> Decimal | None:
        """What was taken off the list price, if anything."""
        if self.list_price_kes is None:
            return None
        difference = self.list_price_kes - self.amount_kes
        return difference if difference > 0 else None

    def is_expired(self, today: date | None = None) -> bool:
        """
        Past its date and still awaiting a decision.

        A method taking `today` rather than a property, for the same reason as
        Invoice.is_overdue: expiry is a claim about a moment, and a test that
        cannot choose the moment passes in the morning and fails at night.
        """
        if self.status not in (self.Status.SENT, self.Status.DRAFT):
            return False
        return self.expires_on < (today or timezone.localdate())

    @property
    def is_open(self) -> bool:
        return self.status in (self.Status.DRAFT, self.Status.SENT)


class Task(models.Model):
    """
    A piece of work assigned to somebody here.

    ══════════════════════════════════════════════════════════════════════════
    THIS IS NOT A PROJECT PLAN, AND IT MUST NOT BECOME ONE.

    Delivery is already tracked: Order has milestones, DeliveryGate has the six
    gates, Blocker has what is stuck and who is waiting on whom. Those describe
    the CLIENT'S work and the client can see them.

    This is the internal layer above that — "Asha is writing the SOW for
    GM-2026-0004 by Thursday" — which no client should see and which does not
    belong in a milestone. Keeping them separate is what stops a client-facing
    delivery record filling up with internal chores.
    ══════════════════════════════════════════════════════════════════════════

    ── WHY IT CAN POINT AT AN ORDER BUT NEVER REQUIRES ONE ─────────────────────

    Plenty of real work is not against an order: chasing a supplier, writing a
    policy, fixing the backup script. Forcing a task to belong to an order
    would mean either inventing one or not writing the task down.
    """

    class Status(models.TextChoices):
        TODO = "todo", "To do"
        DOING = "doing", "In progress"
        BLOCKED = "blocked", "Blocked"
        DONE = "done", "Done"

    class Priority(models.TextChoices):
        # Three, on purpose. Five levels means everything is a 4.
        LOW = "low", "Low"
        NORMAL = "normal", "Normal"
        HIGH = "high", "High"

    title = models.CharField(max_length=200)
    detail = models.TextField(blank=True)

    assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="tasks",
        limit_choices_to={"is_staff": True},
        help_text="One person. Work assigned to everyone is assigned to nobody.",
    )
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="tasks_assigned",
        limit_choices_to={"is_staff": True},
    )

    # ── what this work is about ─────────────────────────────────────────────
    #
    # ══════════════════════════════════════════════════════════════════════
    # FOUR OPTIONAL LINKS, AND EVERY ONE OF THEM IS OPTIONAL ON PURPOSE.
    #
    # A task board that demands to know which project a task belongs to is a
    # board people stop using, because a real day contains "chase the KRA PIN"
    # and "write the retainer proposal" alongside project work. The links exist
    # so a task can be FOUND from the thing it is about — open a client and see
    # what is outstanding for them — not so that every task is filed.
    #
    # They are also how this stops being a second to-do list living beside the
    # company's actual records. A task hanging off nothing is a note; a task
    # hanging off a support ticket is the answer somebody is waiting for.
    # ══════════════════════════════════════════════════════════════════════

    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="tasks",
        help_text="Optional. Plenty of real work is not against an order.",
    )

    # The client, when the work is for one but not against a specific order —
    # a retainer conversation, chasing a renewal, preparing a quote.
    organisation = models.ForeignKey(
        Organisation,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="tasks",
    )

    # SET_NULL rather than CASCADE, unlike the two above. A ticket can be
    # deleted or a decision reversed; the work somebody did about it happened
    # either way, and deleting the task would erase a person's day to tidy a
    # reference.
    ticket = models.ForeignKey(
        "SupportTicket",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tasks",
        help_text="The support request this is the answer to.",
    )
    decision = models.ForeignKey(
        "Decision",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tasks",
        help_text="A decision that created work — the follow-through it commits us to.",
    )

    # The conversation this came out of. Provenance rather than filing: six
    # weeks on, "why am I doing this" is answered by the call it came from,
    # and a task whose origin is a WhatsApp message nobody can find is a task
    # people quietly close.
    contact = models.ForeignKey(
        "ContactLogEntry",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tasks",
    )

    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.TODO
    )
    priority = models.CharField(
        max_length=8, choices=Priority.choices, default=Priority.NORMAL
    )
    due_on = models.DateField(null=True, blank=True)

    done_at = models.DateTimeField(null=True, blank=True)
    blocked_reason = models.CharField(
        max_length=300,
        blank=True,
        help_text="Blocked on what. A blocked task with no reason is a stalled one.",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["status", "due_on", "-priority", "id"]
        indexes = [
            models.Index(fields=["assignee", "status"], name="task_assignee_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.title} → {self.assignee}"

    @property
    def is_open(self) -> bool:
        return self.status != self.Status.DONE

    def is_overdue(self, today: date | None = None) -> bool:
        if not self.is_open or not self.due_on:
            return False
        return self.due_on < (today or timezone.localdate())


class System(models.Model):
    """
    An application, tool or service that Genmars runs or oversees.

    ══════════════════════════════════════════════════════════════════════════
    THIS PORTAL IS THE PARENT. THAT IS A CLAIM WITH OBLIGATIONS.

    Calling something a control plane is easy; what makes it one is that every
    system underneath it is REGISTERED here, with a named owner, a stated
    criticality and somewhere its health is actually checked. A registry that
    lists nine of eleven systems is worse than none, because it invites the
    belief that the two missing ones do not exist.

    So the fields that would be tempting to leave blank — owner, criticality,
    what breaks if it stops — are the ones that are required.
    ══════════════════════════════════════════════════════════════════════════

    ── THE PARENT WATCHES; IT DOES NOT REACH IN ────────────────────────────────

    Registering a system here grants Genmars no ability to execute anything
    inside it. There is deliberately no "run command" and no stored deployment
    credential. What flows is INWARD: a child reports its health and its
    events, and this reads them.

    That direction is a security decision, not a missing feature. A parent that
    could execute inside every child would make one compromised operations
    account a compromise of every system the company touches — including
    client-owned ones, which Charter 05 §V does not permit us to put at risk
    for our own convenience.

    ── A CLIENT-OWNED SYSTEM IS STILL THE CLIENT'S ─────────────────────────────

    `organisation` marks systems we run for a client rather than for ourselves.
    Charter 04 §V: client-owned software carries the client's brand, and the
    same principle applies to their infrastructure. We monitor it because we
    were asked to; that does not make it ours.
    """

    class Kind(models.TextChoices):
        INTERNAL = "internal", "Internal tool"
        CLIENT = "client", "Client system"
        PRODUCT = "product", "Genmars product"
        VENDOR = "vendor", "Third-party service"

    class Status(models.TextChoices):
        LIVE = "live", "Live"
        BUILDING = "building", "Being built"
        PAUSED = "paused", "Paused"
        RETIRED = "retired", "Retired"

    class Criticality(models.TextChoices):
        # Deliberately mapped onto the incident severities the website
        # publishes, so "what does breaking this cost" has one vocabulary.
        CRITICAL = "critical", "Critical — an outage is a SEV-1"
        IMPORTANT = "important", "Important — an outage is a SEV-2"
        MINOR = "minor", "Minor — an outage is a SEV-3"

    class Health(models.TextChoices):
        UNKNOWN = "unknown", "Not checked"
        UP = "up", "Up"
        DOWN = "down", "Down"
        # Answered, but not the way it should. Distinct from DOWN because a
        # 500 and a refused connection are different problems.
        DEGRADED = "degraded", "Degraded"

    name = models.CharField(max_length=120)
    slug = models.SlugField(max_length=120, unique=True)
    kind = models.CharField(max_length=16, choices=Kind.choices)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.LIVE
    )
    criticality = models.CharField(max_length=16, choices=Criticality.choices)

    purpose = models.CharField(
        max_length=300, help_text="What it does, in one line."
    )
    # Required on purpose. "What breaks if this stops" is the question that
    # turns a list of names into something you can triage from at 2am.
    impact_if_down = models.CharField(
        max_length=300,
        help_text="What stops working, and for whom, if this goes down.",
    )

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="systems_owned",
        limit_choices_to={"is_staff": True},
        help_text="One person. A system owned by the team is owned by nobody.",
    )
    organisation = models.ForeignKey(
        Organisation,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="systems",
        help_text="Set where we run this FOR a client. It remains theirs.",
    )

    url = models.URLField(blank=True, help_text="Where a person goes to use it.")
    health_url = models.URLField(
        blank=True,
        help_text=(
            "An endpoint that answers 200 when the system is well. Must need "
            "no authentication and expose nothing."
        ),
    )
    repository = models.CharField(max_length=200, blank=True)
    runbook = models.TextField(
        blank=True, help_text="How to restart it, and what to check first."
    )

    # ---- health, as last observed ----
    health = models.CharField(
        max_length=16, choices=Health.choices, default=Health.UNKNOWN
    )
    health_detail = models.CharField(max_length=300, blank=True)
    checked_at = models.DateTimeField(null=True, blank=True)
    # Set by the child reporting in, rather than by us polling it. Both are
    # useful: one proves we can reach it, the other proves it is running.
    heartbeat_at = models.DateTimeField(null=True, blank=True)
    version = models.CharField(
        max_length=60, blank=True, help_text="Whatever the system reports of itself."
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["criticality", "name"]

    def __str__(self) -> str:
        return self.name

    @property
    def is_watched(self) -> bool:
        """We have some way of knowing whether it is alive."""
        return bool(self.health_url) or self.heartbeat_at is not None

    def security_tier_met(self) -> str | None:
        """
        The highest tier where EVERY requirement is satisfied.

        Sequential on purpose: Tier 2 without Tier 1 is not Tier 2, it is a
        system with an audit log and no backups. Returning the highest tier
        that individually passed would let a gap in the foundation be hidden
        by work done further up.

        None means Tier 1 is not met — which for a live client system is a
        gate violation, since the published page states Tier 1 as the bar
        before any client system goes live.
        """
        from portal.models import SecurityCheck  # local: same module at import

        checks = list(self.security_checks.all())
        if not checks:
            return None

        reached = None
        for tier in (
            SecurityCheck.Tier.ONE,
            SecurityCheck.Tier.TWO,
            SecurityCheck.Tier.THREE,
        ):
            at_tier = [c for c in checks if c.tier == tier]
            if not at_tier or not all(c.is_satisfied for c in at_tier):
                break
            reached = tier
        return reached

    def fails_tier_one(self) -> bool:
        """
        Live, client-facing, and below the bar we publish for going live.

        The one thing on this model that is a stated commitment rather than an
        observation, so it is computed rather than left to somebody noticing.
        """
        if self.status != self.Status.LIVE:
            return False
        if not self.security_checks.exists():
            # Never assessed. Not the same as failing, and not the same as
            # passing — the caller decides how to show it, and the ops screen
            # shows it as unassessed rather than green.
            return False
        return self.security_tier_met() is None

    def heartbeat_is_stale(self, *, minutes: int = 30, now=None) -> bool:
        """
        It reported in once and has since gone quiet.

        Distinct from never having reported: a system that has never sent a
        heartbeat may simply not be instrumented, which is a gap in our
        knowledge rather than evidence of a fault.
        """
        if self.heartbeat_at is None:
            return False
        moment = now or timezone.now()
        return (moment - self.heartbeat_at) > timedelta(minutes=minutes)


class SystemKey(models.Model):
    """
    A credential a registered system uses to report in.

    ══════════════════════════════════════════════════════════════════════════
    HASHED, NEVER STORED. SHOWN ONCE.

    The token is put through the same password hashers as a user password —
    Argon2 first — and only the hash is kept. Nobody, including us, can read it
    back afterwards. That is why creation returns it exactly once and the
    interface says so.

    A token stored in plain text is a token that leaks with one database dump,
    one backup left on a laptop, or one operations account. This system already
    keeps client contracts and payment records; a plaintext credential table
    beside them would be the softest thing in the building.
    ══════════════════════════════════════════════════════════════════════════

    ── WHY THERE IS A PREFIX ───────────────────────────────────────────────────

    A hash cannot be looked up. Without something searchable, authenticating a
    request would mean hashing the presented token against EVERY key in the
    table, which is slow and gets slower. The prefix is the first characters of
    the token, stored in clear, and is only an index — it is far too short to
    be useful on its own.

    ── WHAT A KEY CAN DO ───────────────────────────────────────────────────────

    Report health and post events about ITS OWN system. Nothing else. It cannot
    read another system, cannot touch client data, and cannot act on the
    portal. See portal/system_api.py.
    """

    system = models.ForeignKey(System, on_delete=models.CASCADE, related_name="keys")
    label = models.CharField(
        max_length=120, help_text="Where this key lives — 'production container'."
    )

    prefix = models.CharField(max_length=12, db_index=True)
    hashed = models.CharField(max_length=255)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="system_keys_made",
        limit_choices_to={"is_staff": True},
    )
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    # Revoking keeps the row. "This key was revoked on the 4th" is a fact worth
    # holding on to; deleting it makes an incident harder to reconstruct.
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.system.slug}/{self.label}"

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None


class SystemEvent(models.Model):
    """
    Something a registered system reported about itself.

    ══════════════════════════════════════════════════════════════════════════
    THIS IS UNTRUSTED INPUT AND IT IS NEVER A COMMAND.

    Everything here arrived over the network from another application. It is
    DATA: recorded, displayed, and acted on by a human who decides what to do.
    Nothing in this codebase reads a SystemEvent and takes an action because of
    what it says, and nothing should — an event that could trigger behaviour
    turns every child system into a way to drive the parent.

    Text is stored and rendered as text. It is never evaluated, never used to
    build a query, and never treated as a path or a URL.
    ══════════════════════════════════════════════════════════════════════════
    """

    class Level(models.TextChoices):
        INFO = "info", "Info"
        WARNING = "warning", "Warning"
        ERROR = "error", "Error"

    system = models.ForeignKey(System, on_delete=models.CASCADE, related_name="events")
    level = models.CharField(max_length=16, choices=Level.choices, default=Level.INFO)
    message = models.CharField(max_length=300)
    detail = models.JSONField(default=dict, blank=True)

    # When the child says it happened, versus when we received it. They differ
    # when a system buffers events through an outage, which is exactly when the
    # difference matters.
    occurred_at = models.DateTimeField(null=True, blank=True)
    received_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-received_at", "-id"]
        indexes = [
            models.Index(fields=["system", "-received_at"], name="system_event_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.system.slug}: {self.message[:60]}"


class SupportTicket(models.Model):
    """
    A client asking for help.

    ══════════════════════════════════════════════════════════════════════════
    NOTHING HERE PROMISES A RESPONSE TIME.

    Charter 03 §IV is a standing rule: never put a commitment in front of a
    client that has not been tested under real conditions. Support is where
    that rule is hardest to keep, because "we usually reply within a few hours"
    is easy to type and becomes a promise the moment a client reads it.

    So there is no SLA field, no target, and no countdown. What there is
    instead is `first_answered_at`, recorded automatically — a measurement of
    what actually happens rather than a claim about what will. When there is
    enough of it to state something true, it can be stated. Not before.
    ══════════════════════════════════════════════════════════════════════════

    ── PRIORITY IS SET BY US, NOT BY THE CLIENT ────────────────────────────────

    Every client-set priority field ends up with everything marked urgent,
    which is the same as nothing being urgent. The client says what is
    happening and how it affects them; someone here reads that and decides.
    """

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        # We have replied and are waiting on them. Distinct from open, because
        # a queue that cannot tell those apart looks permanently on fire.
        WAITING = "waiting", "Waiting on the client"
        ANSWERED = "answered", "Answered"
        RESOLVED = "resolved", "Resolved"

    class Priority(models.TextChoices):
        LOW = "low", "Low"
        NORMAL = "normal", "Normal"
        URGENT = "urgent", "Urgent"

    reference = models.CharField(max_length=32, unique=True, db_index=True)
    organisation = models.ForeignKey(
        Organisation, on_delete=models.CASCADE, related_name="tickets"
    )
    raised_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="tickets_raised"
    )
    order = models.ForeignKey(
        Order,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tickets",
        help_text="Optional. Not every question is about a project.",
    )

    subject = models.CharField(max_length=200)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.OPEN
    )
    priority = models.CharField(
        max_length=8, choices=Priority.choices, default=Priority.NORMAL
    )

    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tickets_assigned",
        limit_choices_to={"is_staff": True},
    )

    created_at = models.DateTimeField(auto_now_add=True)
    # Measured, never promised. See the docstring.
    first_answered_at = models.DateTimeField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["organisation", "status"], name="ticket_client_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.reference} — {self.subject}"

    @property
    def is_open(self) -> bool:
        return self.status != self.Status.RESOLVED

    def waited_for_first_answer(self) -> timedelta | None:
        """How long they waited the first time. The only honest number we have."""
        if self.first_answered_at is None:
            return None
        return self.first_answered_at - self.created_at


class SupportMessage(models.Model):
    """
    One message on a ticket, from either side.

    ══════════════════════════════════════════════════════════════════════════
    `internal` IS THE MOST DANGEROUS FIELD IN THIS FILE.

    An internal note is written by staff about a client, in the knowledge that
    the client cannot see it. If that filter is ever wrong, a client reads what
    we said about them — which is worse than a data leak, because it is a leak
    of opinion.

    So the client serializer excludes them, the client selector excludes them,
    and there is a test that writes one and asserts it is absent from the
    client's view of the thread. That test exists to fail loudly the day
    somebody adds a convenient shortcut.
    ══════════════════════════════════════════════════════════════════════════
    """

    ticket = models.ForeignKey(
        SupportTicket, on_delete=models.CASCADE, related_name="messages"
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="support_messages",
    )
    # Kept because SET_NULL loses the name, and an unattributed reply on a
    # support thread is worse than no thread.
    author_label = models.CharField(max_length=200, blank=True)
    from_staff = models.BooleanField(default=False)

    body = models.TextField()
    internal = models.BooleanField(
        default=False,
        help_text="A note between us. NEVER shown to the client — see the docstring.",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]

    def __str__(self) -> str:
        return f"{self.ticket.reference}: {self.body[:50]}"


class SecurityCheck(models.Model):
    """
    Whether one system meets one published security requirement.

    ══════════════════════════════════════════════════════════════════════════
    genmars.co.ke/approach CALLS THESE GATES, NOT A WISH LIST.

    Fifteen requirements across three tiers, published to anyone who reads the
    page, with Tier 1 stated as the bar "before any client system goes live".
    Until now nothing recorded whether a single system met a single one of
    them, which makes "gate" a description of an intention rather than of
    anything that happens.

    Charter 04 §IV: nothing untrue on a Genmars surface. A gate that nothing
    checks is the same kind of claim as a post-mortem nobody writes.
    ══════════════════════════════════════════════════════════════════════════

    ── FOUR STATES, AND "PARTIAL" EARNS ITS PLACE ──────────────────────────────

    Most of these are not binary in practice. "Personal data encrypted at rest"
    is true of our off-box backups and not of the live database — recording
    that as MET would be a lie and as NOT MET would be useless, because it
    hides that most of the work is done and names none of it.

    So PARTIAL exists, and it is the state that requires a note. A partial with
    no explanation of what is missing is worse than a plain no: it looks like
    progress and cannot be acted on.

    ── NOT_APPLICABLE IS THE DANGEROUS ONE ─────────────────────────────────────

    It is the state that makes a red board go green without anything changing.
    It also requires a note, and the note has to say why the requirement does
    not apply to THIS system — not that it is inconvenient.
    """

    class Tier(models.TextChoices):
        # Wording and order copied from the published page. If these ever
        # disagree with the website, the website is right and this is the bug.
        ONE = "tier1", "Tier 1 — before any client system goes live"
        TWO = "tier2", "Tier 2 — within 90 days of the first paying user"
        THREE = "tier3", "Tier 3 — before making enterprise claims"

    class Status(models.TextChoices):
        NOT_MET = "not_met", "Not met"
        PARTIAL = "partial", "Partly met"
        MET = "met", "Met"
        NOT_APPLICABLE = "n_a", "Does not apply"

    system = models.ForeignKey(
        System, on_delete=models.CASCADE, related_name="security_checks"
    )
    tier = models.CharField(max_length=8, choices=Tier.choices)
    # The requirement in the website's own words, copied rather than referenced,
    # so a check recorded today still says what was being assessed if the page
    # is later reworded.
    item = models.CharField(max_length=200)
    position = models.PositiveSmallIntegerField(default=0)

    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.NOT_MET
    )
    note = models.TextField(
        blank=True,
        help_text=(
            "Evidence when met; what is missing when partial; why it does not "
            "apply when marked so."
        ),
    )

    assessed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="security_checks",
        limit_choices_to={"is_staff": True},
    )
    assessed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["system", "tier", "position", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["system", "tier", "position"],
                name="one_check_per_requirement_per_system",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.system.slug} — {self.item[:50]} ({self.get_status_display()})"

    @property
    def is_satisfied(self) -> bool:
        """
        Met, or genuinely not applicable.

        Partial is deliberately NOT satisfied. A tier is a gate, and mostly
        through a gate is on the wrong side of it.
        """
        return self.status in (self.Status.MET, self.Status.NOT_APPLICABLE)

    @property
    def needs_a_note(self) -> bool:
        """
        Partial and not-applicable are claims that require an explanation.

        Met does not: the evidence is welcome but the requirement itself says
        what was needed. Not-met does not either — the absence is the note.
        """
        return (
            self.status in (self.Status.PARTIAL, self.Status.NOT_APPLICABLE)
            and not self.note.strip()
        )



class BillingProfile(models.Model):
    """
    Who Genmars is, on a document somebody pays against.

    ══════════════════════════════════════════════════════════════════════════
    A SINGLE ROW. There is one company, so there is one of these.

    Enforced by `pk = 1` on save rather than by a `singleton` boolean somebody
    could set twice. A second billing identity is not a feature request, it is
    a bug: two rows here means two different paybills can appear on invoices
    depending on which one a query happened to return first, and nobody would
    notice until a client paid the wrong one.
    ══════════════════════════════════════════════════════════════════════════

    ── WHY THIS IS A MODEL AND NOT JUST THE ENV VARS ──────────────────────────

    These arrived as BILLING_* settings, which was right while nobody had the
    values: a setting with no value is obviously unset. But they are business
    facts, not deployment facts. A KRA PIN does not change per environment, it
    is not a secret, and asking the founder to edit a .env on a server and
    restart gunicorn to correct a typo in a postal address is how a wrong
    address stays on invoices for a month.

    So this holds them, and `resolve()` below falls back to the settings for
    any field left blank. Nothing breaks on the way through, and the env vars
    remain a valid way to set a value — they simply stop being the only one.

    ── BLANK STILL MEANS OMITTED, NEVER GUESSED ───────────────────────────────

    Unchanged from the settings, and it is the point of the whole arrangement:
    an invoice shows what it has been told and no more. A document with no
    payment details says so plainly and points at the named contact, which is
    survivable. One carrying a plausible-looking wrong paybill is not, because
    it is paid, and the money goes somewhere else (Charter 04 §IV).

    ── WHO MAY EDIT IT ────────────────────────────────────────────────────────

    Founder only — see operations/permissions.py. Changing the bank details on
    every future invoice is the single highest-value write in this system, and
    `updated_by` records who made it, because a payment-detail change nobody
    can attribute is indistinguishable from a compromise.
    """

    legal_name = models.CharField(
        max_length=200,
        blank=True,
        help_text="The registered name, as it should appear on an invoice.",
    )
    email = models.EmailField(
        blank=True,
        help_text="Where billing questions go. Shown on every invoice.",
    )
    kra_pin = models.CharField(
        max_length=20,
        blank=True,
        help_text="Required on a Kenyan tax invoice. Omitted from the document until set.",
    )
    postal_address = models.CharField(max_length=300, blank=True)

    mpesa_paybill = models.CharField(
        max_length=20,
        blank=True,
        help_text="Paybill or till number.",
    )
    # {number} is replaced with the invoice number. Putting the invoice number
    # in the account field is what makes a payment reconcilable without a
    # phone call, so the placeholder is the default rather than a suggestion.
    mpesa_account_hint = models.CharField(
        max_length=60,
        blank=True,
        help_text="What the client types in the account field. {number} becomes the invoice number.",
    )
    # One free-text field rather than five, because bank details vary in shape
    # and a rigid schema would force a wrong one.
    bank_details = models.TextField(
        blank=True,
        help_text="Bank, branch, account name and number, as they should be typed.",
    )
    terms = models.TextField(blank=True)

    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        limit_choices_to={"is_staff": True},
    )

    class Meta:
        verbose_name = "billing profile"
        verbose_name_plural = "billing profile"

    def __str__(self) -> str:
        return self.legal_name or "Billing profile (unset)"

    def save(self, *args, **kwargs):
        # The singleton, enforced where it cannot be forgotten. Not in a
        # serializer, not in a view: any code path that saves one of these
        # saves the one that exists.
        self.pk = 1
        # `objects.create()` asks for force_insert, which on the second call
        # would raise UNIQUE rather than update the row — an IntegrityError
        # from a model whose whole promise is that there is only one of it.
        # Dropped so that every write to this table is an upsert of row 1.
        kwargs.pop("force_insert", None)
        super().save(*args, **kwargs)

    @classmethod
    def load(cls) -> "BillingProfile":
        """
        The profile, creating an empty one the first time.

        Never returns None. A caller that had to handle "no profile yet"
        separately from "profile with blank fields" would have two code paths
        for the same situation, and the rarer one would be the one with the bug.
        """
        profile, _ = cls.objects.get_or_create(pk=1)
        return profile

    def resolve(self, field: str) -> str:
        """
        This profile's value for `field`, or the configured default.

        Falls back to `settings.BILLING_<FIELD>` when the stored value is
        blank, so the env vars keep working and a half-filled profile is not
        worse than an empty one.
        """
        stored = (getattr(self, field, "") or "").strip()
        if stored:
            return stored
        return (getattr(settings, f"BILLING_{field.upper()}", "") or "").strip()

    def account_reference(self, invoice_number: str) -> str | None:
        """
        What to type in the M-Pesa account field for this invoice.

        None when there is no paybill: an account reference for a paybill that
        does not exist is an instruction to pay nowhere.
        """
        if not self.resolve("mpesa_paybill"):
            return None
        hint = self.resolve("mpesa_account_hint") or "{number}"
        return hint.replace("{number}", invoice_number)


# ═════════════════════════════════════════════════════════════════════════════
# THE WORKROOM — what the company itself does all day
# ═════════════════════════════════════════════════════════════════════════════
#
# Everything above this line is about a CLIENT: their order, their invoice,
# their ticket. The two models below are about US, and they are here rather
# than in a new app for the reason Charter 03 §I gives — an app boundary is a
# thing entering the stack, and these rows are read alongside orders and
# activity on the same screens, by the same selectors, behind the same
# `is_staff` gate.


class Shift(models.Model):
    """
    One stretch of somebody being at work. Clock in, clock out.

    ══════════════════════════════════════════════════════════════════════════
    THIS IS A RECORD, NOT A SUPERVISOR.

    It measures nothing about how hard anybody worked and it must never be
    made to. There is no idle timer, no screenshot, no keystroke count, and
    adding one would change what this company is — Charter 01. What it answers
    is narrower and genuinely useful in a company where two people are often
    somewhere else: is anyone around right now, and where did last week go.
    ══════════════════════════════════════════════════════════════════════════

    ── ONE OPEN SHIFT PER PERSON, ENFORCED IN THE DATABASE ─────────────────────

    Not in the service layer. Two taps on a phone with a slow connection is the
    ordinary way you end up clocked in twice, and the second row would double
    every hour that person worked that day. A partial unique index refuses it
    at the point the row is written, which is the only place a race can be
    settled.

    ── NOBODY CLOCKS ANYBODY ELSE ──────────────────────────────────────────────

    There is no endpoint that takes a person. `person` is always the requesting
    account, set by the service from `actor`. A founder cannot clock a delivery
    engineer in, and that is not an oversight: a timesheet somebody else can
    write is not a timesheet, it is an assertion about a person made in their
    name.
    """

    person = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="shifts",
        limit_choices_to={"is_staff": True},
    )

    started_at = models.DateTimeField(default=timezone.now, db_index=True)
    ended_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Null means still clocked in. There is exactly one of these per person.",
    )

    # What you are on. Optional, one line, and it is the part that makes this
    # worth reading back — "hours on Tuesday" tells you nothing a month later.
    started_note = models.CharField(max_length=200, blank=True)
    ended_note = models.CharField(max_length=200, blank=True)

    # True when the shift was closed with a time typed in rather than "now",
    # which happens when somebody forgot. Kept because an hour figure that was
    # remembered is a weaker fact than one that was measured, and a timesheet
    # that hides the difference invites the reader to trust both equally.
    ended_late = models.BooleanField(default=False)

    class Meta:
        ordering = ["-started_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["person"],
                condition=models.Q(ended_at__isnull=True),
                name="one_open_shift_per_person",
            ),
            models.CheckConstraint(
                # A shift that ends before it starts is negative hours in every
                # total that reads it, and the totals are the whole point.
                condition=models.Q(ended_at__isnull=True)
                | models.Q(ended_at__gt=models.F("started_at")),
                name="shift_ends_after_it_starts",
            ),
        ]
        indexes = [models.Index(fields=["person", "-started_at"], name="shift_person_idx")]

    def __str__(self) -> str:
        return f"{self.person_id} {self.started_at:%Y-%m-%d %H:%M}"

    @property
    def is_open(self) -> bool:
        return self.ended_at is None

    @property
    def minutes(self) -> int:
        """Length so far. An open shift is measured to now, not to zero."""
        end = self.ended_at or timezone.now()
        return max(0, int((end - self.started_at).total_seconds() // 60))

    @property
    def local_date(self) -> date:
        """
        The day this shift BELONGS to — the day it started, in Nairobi.

        Deliberately the start and not the end. A shift that runs past midnight
        is one day's work finished late, and splitting it across two dates
        would put an hour of Monday into Tuesday's total and break Monday's
        place in a streak.
        """
        return timezone.localtime(self.started_at).date()


class Decision(models.Model):
    """
    Why we did it that way.

    ══════════════════════════════════════════════════════════════════════════
    THE REGISTER EXISTS BECAUSE REASONS EVAPORATE AND CHOICES DO NOT.

    Six months on, the choice is still in the codebase, in the pricing, in the
    contract — and the reasoning behind it is in somebody's memory, if it is
    anywhere. What follows is the expensive failure: the constraint that forced
    the choice is forgotten, the choice starts looking arbitrary, somebody
    undoes it, and the original problem comes back wearing different clothes.

    So `context` is not optional here. A register that records what was decided
    without what was true at the time is a list of arbitrary-looking choices,
    which is worse than nothing because it invites exactly that undoing.
    ══════════════════════════════════════════════════════════════════════════

    ── A DECIDED ENTRY IS NOT EDITED ───────────────────────────────────────────

    Same discipline as ActivityLog, Contract and Invoice, and for the same
    reason: a record you can quietly rewrite is a draft. A decision that turned
    out wrong is REVERSED with the reason attached, and a decision that has
    moved on is SUPERSEDED by a new entry that points back at it. Both leave
    the original readable, which is the only version of this that helps anyone
    — the wrong turns are most of the value.

    A PROPOSED entry is still editable. It has not been relied on yet.

    ── NOT A PERMISSION SYSTEM ─────────────────────────────────────────────────

    Any staff account may write here. The authority to make a given decision
    lives where Charter 02 §I put it and is enforced by the endpoint that acts
    on it — issuing a contract, changing a price, converting an enquiry. Gating
    the WRITING DOWN of a decision on rank would only mean decisions get made
    and not written down, which is the failure this exists to prevent.
    """

    class Status(models.TextChoices):
        PROPOSED = "proposed", "Proposed"
        DECIDED = "decided", "Decided"
        SUPERSEDED = "superseded", "Superseded"
        REVERSED = "reversed", "Reversed"

    reference = models.CharField(max_length=32, unique=True, db_index=True)
    title = models.CharField(max_length=200, help_text="The choice, in one line.")

    context = models.TextField(
        help_text="What was true at the time that forced a choice. Required — see the docstring."
    )
    options = models.TextField(
        blank=True,
        help_text="What else was on the table. Blank is honest when there genuinely was one option.",
    )
    decision = models.TextField(help_text="What we are doing.")
    consequences = models.TextField(
        blank=True, help_text="What this commits us to, including what it costs."
    )
    revisit_when = models.CharField(
        max_length=300,
        blank=True,
        help_text=(
            "What would make this worth reopening — a client size, a price, a "
            "date. The difference between a decision and a dogma."
        ),
    )

    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.PROPOSED, db_index=True
    )

    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="decisions",
        limit_choices_to={"is_staff": True},
    )
    # Kept alongside the FK for the ActivityLog reason: SET_NULL loses the name,
    # and "somebody decided this" is a materially worse record than "Asha did".
    decided_by_label = models.CharField(max_length=200, blank=True)
    decided_on = models.DateField(null=True, blank=True)

    supersedes = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="superseded_by",
    )

    # Why it was reversed. Empty on everything else. A reversal with no reason
    # is the one entry in this register that teaches nothing.
    reversal_reason = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-decided_on", "-created_at", "-id"]
        indexes = [models.Index(fields=["status", "-created_at"], name="decision_status_idx")]

    def __str__(self) -> str:
        return f"{self.reference} {self.title}"

    @property
    def is_open_to_edits(self) -> bool:
        """Only a proposal. Everything else is superseded or reversed, never rewritten."""
        return self.status == self.Status.PROPOSED

    @property
    def is_in_force(self) -> bool:
        return self.status == self.Status.DECIDED


# ═════════════════════════════════════════════════════════════════════════════
# THE CLIENT RECORD — who they are, what we run for them, and what was said
# ═════════════════════════════════════════════════════════════════════════════
#
# Organisation (accounts/models.py) is IDENTITY: a name, and the thing
# memberships hang off. It stays that way. Everything below is the commercial
# and operational relationship, and it lives here because putting a client's
# phone number and renewal dates in the identity app would make `accounts`
# the place where everything about a client accumulates — which is how the
# module that authentication depends on becomes the one nobody can change
# safely.


class ClientProfile(models.Model):
    """
    Who this client actually is, beyond a row in an organisations table.

    ══════════════════════════════════════════════════════════════════════════
    THE FILE THAT LIVES IN SOMEBODY'S PHONE TODAY.

    Right now the spa's owner's number, the fact that she prefers WhatsApp to
    email, and what her business does are in one person's head and one
    person's contacts app. That is fine until that person is on a plane, and
    it is the ordinary way a three-person company loses continuity: nothing
    dramatic happens, somebody simply cannot answer a question they should
    have been able to.
    ══════════════════════════════════════════════════════════════════════════

    ── NOT A CRM, AND IT MUST NOT GROW INTO ONE ────────────────────────────────

    No pipeline stages, no lead scoring, no last-touched-by, no next-action
    dates on the client itself. Those belong to work and are already modelled:
    an Enquiry is a request, an Order is work, a ContactLogEntry is a
    conversation. What is here is only the part that is true about the client
    regardless of what is happening this week.
    """

    class Channel(models.TextChoices):
        """
        How this person actually wants to be reached.

        WhatsApp first, and deliberately: it is how most small Kenyan
        businesses communicate, and emailing a client who reads WhatsApp is a
        message that technically was sent. Charter 05 §I is about what the
        client was actually TOLD, and a channel they do not read does not tell
        them anything.
        """

        WHATSAPP = "whatsapp", "WhatsApp"
        CALL = "call", "Phone call"
        EMAIL = "email", "Email"
        SMS = "sms", "SMS"
        IN_PERSON = "in_person", "In person"

    organisation = models.OneToOneField(
        Organisation, on_delete=models.CASCADE, related_name="profile"
    )

    what_they_do = models.CharField(
        max_length=200,
        blank=True,
        help_text="One line. 'A day spa in Kilimani', not a mission statement.",
    )
    website = models.URLField(blank=True)

    # The person, not the company. Small businesses are one person who decides,
    # and "the client approved it" is worth nothing without their name on it.
    contact_name = models.CharField(max_length=200, blank=True)
    contact_role = models.CharField(max_length=120, blank=True)
    contact_phone = models.CharField(
        max_length=40,
        blank=True,
        help_text="As you would dial it. +254… — the country code is not optional.",
    )
    contact_email = models.EmailField(blank=True)

    preferred_channel = models.CharField(
        max_length=16, choices=Channel.choices, blank=True
    )

    client_since = models.DateField(
        null=True, blank=True, help_text="When we started working with them."
    )

    notes = models.TextField(
        blank=True,
        help_text=(
            "Standing context: how they like to work, what has gone wrong "
            "before, who else needs to be in the room. NOT a diary — a "
            "conversation goes in the contact log."
        ),
    )

    # ── Charter 04 §V, recorded where the fact belongs ──────────────────────
    #
    # "Client-owned software carries the client's brand; Genmars is credited
    # only with WRITTEN PERMISSION." Until now that permission has been a
    # boolean in gen-website/src/lib/company.ts, which is a build-time constant
    # in a different repository — so the record of a promise a client made
    # lived in a file nobody would think to look in.
    #
    # The website still reads its own constant, because it is a static export
    # and cannot query this. This is the RECORD; that constant is a copy of it,
    # and `permission_note` is where the evidence is named.
    may_be_named = models.BooleanField(
        default=False,
        help_text="Have they agreed IN WRITING that we may name them publicly?",
    )
    permission_note = models.CharField(
        max_length=300,
        blank=True,
        help_text="Where the written permission is — the file in 07-executed/, the date of the email.",
    )

    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"Profile for {self.organisation.name}"


class HostingArrangement(models.Model):
    """
    Something we run, hold or renew on a client's behalf.

    ══════════════════════════════════════════════════════════════════════════
    THIS EXISTS FOR ONE DATE: `renews_on`.

    A domain lapses quietly. Nothing fails, no alert fires, and then one
    morning a client's site is a registrar parking page and their email stops
    — and to them it is indistinguishable from us having broken something.
    Recovering a lapsed .co.ke is slow, sometimes expensive, and occasionally
    impossible if somebody else takes it.

    Every other field here is context for that one. The renewal count is
    carried on the operations queue for the same reason the weekly-note count
    is: a date nobody is looking at is the failure mode.
    ══════════════════════════════════════════════════════════════════════════

    ── `account_holder` IS A CHARTER FIELD, NOT ADMIN ──────────────────────────

    Charter 05 §VIII: "We do not hold data, domains, or accounts hostage under
    any circumstance." A domain registered in Genmars' name is one a client
    cannot take with them without asking us, and the moment that is convenient
    for us is the moment it stops being their asset.

    So it is recorded explicitly, per arrangement, and shown on the client's
    page. Holding one is sometimes the practical answer — a client with no
    card, or no interest — but it must be a decision somebody made and can see,
    not a default nobody noticed.
    """

    class Kind(models.TextChoices):
        DOMAIN = "domain", "Domain name"
        HOSTING = "hosting", "Hosting"
        EMAIL = "email", "Email"
        CERTIFICATE = "certificate", "TLS certificate"
        OTHER = "other", "Other"

    class Holder(models.TextChoices):
        CLIENT = "client", "The client"
        GENMARS = "genmars", "Genmars"

    organisation = models.ForeignKey(
        Organisation, on_delete=models.CASCADE, related_name="hosting"
    )
    # Optional link to the thing we monitor. Kept nullable because plenty of
    # arrangements have no System behind them — a domain we renew for a client
    # whose site somebody else built is still ours to not forget.
    system = models.ForeignKey(
        "System",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="hosting",
    )

    kind = models.CharField(max_length=16, choices=Kind.choices)
    identifier = models.CharField(
        max_length=200,
        help_text="The domain, the plan, the mailbox. 'clipsserenityspa.co.ke'.",
    )
    provider = models.CharField(
        max_length=120, blank=True, help_text="Registrar or host. Truehost, Hetzner, Zoho."
    )

    account_holder = models.CharField(
        max_length=16,
        choices=Holder.choices,
        default=Holder.CLIENT,
        help_text="Whose name the account is in. See this model's docstring — Charter 05 §VIII.",
    )

    renews_on = models.DateField(
        null=True,
        blank=True,
        help_text="The date it lapses if nobody acts. The reason this model exists.",
    )
    auto_renew = models.BooleanField(
        default=False,
        help_text=(
            "Whether the provider will take payment automatically. False does "
            "NOT mean it will lapse — it means a person has to act, which is "
            "exactly what gets forgotten."
        ),
    )

    # What it costs us and what they pay. Both, because the pair is the only
    # way to see an arrangement that is quietly costing us money.
    annual_cost_kes = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True
    )
    annual_charge_kes = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True
    )

    notes = models.TextField(blank=True)
    retired_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Set when we stop running it. Never deleted — the history is the record.",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["renews_on", "identifier"]
        indexes = [
            models.Index(fields=["renews_on"], name="hosting_renewal_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.identifier} ({self.get_kind_display()})"

    @property
    def is_live(self) -> bool:
        return self.retired_at is None

    def days_until_renewal(self, today: date | None = None) -> int | None:
        if self.renews_on is None or not self.is_live:
            return None
        return (self.renews_on - (today or timezone.localdate())).days


class ContactLogEntry(models.Model):
    """
    A conversation with a client, written down.

    ══════════════════════════════════════════════════════════════════════════
    THE GAP THIS FILLS: EVERYTHING THAT IS NOT A TICKET.

    SupportTicket already records a client asking for help THROUGH THE PORTAL.
    But the way work actually arrives here is a WhatsApp message on a Saturday,
    or a call where the owner says "can we add online booking" — and there was
    nowhere for that to go. It stayed in one person's phone, and the company's
    record of its own client relationships was a chat history on a device.

    That fails in a specific, expensive way. Somebody agrees to something on a
    call, nobody writes it down, and three weeks later the client and Genmars
    remember it differently. Neither is lying. There is simply no record, and
    the client is the one who paid.
    ══════════════════════════════════════════════════════════════════════════

    ── `follow_up` IS THE PART THAT EARNS ITS PLACE ────────────────────────────

    "I'll send you a quote on Thursday" is the single most common promise made
    in this company and the single most common one dropped — not through
    neglect, but because it lived only in the memory of whoever said it. An
    uncleared follow-up past its date is carried on the operations queue where
    the weekly-note count is, for the same reason.

    ── INTERNAL. THE CLIENT NEVER SEES THIS ────────────────────────────────────

    Deliberately, and not because there is anything to hide. A log that the
    client reads is a log people write carefully, and a log people write
    carefully stops being written. "Owner sounded fed up with the old booking
    system" is exactly the kind of note that is worth having and would never be
    typed into something she can open.

    What the client is TOLD is a ProgressNote, which is published, dated and
    written for them. Two records, two audiences, and neither pretending to be
    the other — same split as SupportMessage.internal.
    """

    class Channel(models.TextChoices):
        WHATSAPP = "whatsapp", "WhatsApp"
        CALL = "call", "Phone call"
        EMAIL = "email", "Email"
        SMS = "sms", "SMS"
        MEETING = "meeting", "Meeting"
        OTHER = "other", "Other"

    class Direction(models.TextChoices):
        INBOUND = "inbound", "They contacted us"
        OUTBOUND = "outbound", "We contacted them"

    organisation = models.ForeignKey(
        Organisation, on_delete=models.CASCADE, related_name="contact_log"
    )
    # What it was about, when that is a specific thing we already track.
    order = models.ForeignKey(
        "Order",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="contact_log",
    )

    channel = models.CharField(max_length=16, choices=Channel.choices)
    direction = models.CharField(max_length=16, choices=Direction.choices)

    # Editable, and defaulting to now rather than being auto_now_add. A call
    # gets written up afterwards — often the next morning — and a log that
    # stamped everything with the moment somebody found time to type it would
    # put Friday's conversation on Monday and make the order of events wrong.
    happened_at = models.DateTimeField(default=timezone.now, db_index=True)

    # Free text on purpose. The person you spoke to usually has no account here
    # and may never have one; requiring a User would mean the commonest case
    # cannot be recorded at all.
    with_whom = models.CharField(
        max_length=200, blank=True, help_text="Who at the client. A name, not an account."
    )

    summary = models.CharField(max_length=300, help_text="One line. What it was about.")
    detail = models.TextField(blank=True, help_text="What was actually said.")

    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="contact_log",
        limit_choices_to={"is_staff": True},
    )
    # Survives the account being deactivated, for the ActivityLog reason.
    recorded_by_label = models.CharField(max_length=200, blank=True)

    # ── what we owe them as a result ────────────────────────────────────────
    follow_up = models.CharField(
        max_length=300,
        blank=True,
        help_text="What WE said we would do. Blank when the answer is nothing.",
    )
    follow_up_by = models.DateField(null=True, blank=True)
    cleared_at = models.DateTimeField(null=True, blank=True)
    cleared_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="follow_ups_cleared",
        limit_choices_to={"is_staff": True},
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-happened_at", "-id"]
        indexes = [
            models.Index(fields=["organisation", "-happened_at"], name="contact_org_idx"),
            models.Index(fields=["follow_up_by", "cleared_at"], name="contact_followup_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.happened_at:%Y-%m-%d} {self.organisation_id} {self.summary[:40]}"

    @property
    def is_owed(self) -> bool:
        """A promise made and not yet kept."""
        return bool(self.follow_up) and self.cleared_at is None

    def is_overdue(self, today: date | None = None) -> bool:
        if not self.is_owed or self.follow_up_by is None:
            return False
        return self.follow_up_by < (today or timezone.localdate())


def attachment_path(instance: "ContactAttachment", filename: str) -> str:
    """
    Where an uploaded file is stored, and it is NEVER the name it arrived with.

    ══════════════════════════════════════════════════════════════════════════
    THE CLIENT'S FILENAME IS DISPLAY TEXT AND NOTHING ELSE.

    It is attacker-controlled. Used as a path it is `../../etc/passwd` or a
    name that collides with somebody else's document; stored verbatim it also
    leaks — "Kilimani Dental invoice dispute Feb.pdf" tells anyone who sees the
    path something the file itself was going to tell them anyway, but the path
    ends up in logs, backups and error reports where the file does not.

    So the stored name is a random one, and the extension is taken from the
    allowlist in portal/attachments.py rather than from the filename. What the
    client called it lives in `original_name`, as a string, rendered as text.
    ══════════════════════════════════════════════════════════════════════════
    """
    import uuid

    suffix = Path(filename).suffix.lower()[:10]
    # Keep one client's files together, so a deletion request under Charter 05
    # §VIII is a directory rather than a query.
    return f"contact/{instance.entry.organisation_id}/{uuid.uuid4().hex}{suffix}"


class ContactAttachment(models.Model):
    """
    A file or photo that came out of a conversation.

    The commonest one is a photograph: the paper booking sheet a spa still
    runs on, a screenshot of an error, a receipt. Those arrive over WhatsApp
    and used to live on a phone, which meant that the single most useful
    artefact of a scoping conversation was the one thing not in the system.

    ── WHY THERE ARE NO THUMBNAILS ─────────────────────────────────────────────

    Generating them needs Pillow, and Charter 03 §I says a thing enters the
    stack only when what is already there cannot do the job. Browsers scale
    images perfectly well, and the cost of not having thumbnails is some
    bandwidth on a page three people open. The cost of having them is an image
    parser — historically one of the most exploited pieces of code in any
    stack — running on files uploaded from outside.

    ── AND NO CLIENT-FACING ROUTE ──────────────────────────────────────────────

    These hang off ContactLogEntry, which is internal. Same reasoning: the log
    is written honestly because nobody outside Genmars reads it. There is a
    test that the client export cannot carry these.
    """

    entry = models.ForeignKey(
        ContactLogEntry, on_delete=models.CASCADE, related_name="attachments"
    )

    file = models.FileField(upload_to=attachment_path, max_length=300)

    # What the sender called it. Display text — see attachment_path.
    original_name = models.CharField(max_length=255)
    # What we decided it is, from sniffing the bytes — never what the browser
    # claimed. See portal/attachments.py.
    content_type = models.CharField(max_length=100)
    size_bytes = models.PositiveIntegerField()

    caption = models.CharField(
        max_length=300,
        blank=True,
        help_text="What it shows. A photo of a booking sheet is meaningless in a year without this.",
    )

    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="attachments",
        limit_choices_to={"is_staff": True},
    )
    uploaded_by_label = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]

    def __str__(self) -> str:
        return self.original_name

    @property
    def is_image(self) -> bool:
        """Whether the BYTES said it is an image. Used only to decide whether a
        preview is offered, never to decide how it is served."""
        return self.content_type.startswith("image/")


class AccessRequest(models.Model):
    """
    "I need to do something I am not allowed to do. May I?"

    ══════════════════════════════════════════════════════════════════════════
    THE PROBLEM THIS SOLVES IS A 403 WITH NOWHERE TO GO.

    Before this, a delivery engineer who needed a client archived hit a refusal
    and the workflow left the software: a WhatsApp message, a call, a founder
    logging in to do it, and no record anywhere that any of it happened. The
    permission was enforced and the process around it was invisible.

    Worse, that friction is what makes people share passwords. A permission
    model with no way to ask is a permission model somebody eventually routes
    around, and the routing-around is never written down.
    ══════════════════════════════════════════════════════════════════════════

    ── AN APPROVAL IS SINGLE-USE AND IT EXPIRES ────────────────────────────────

    This is the part that makes the whole thing safe rather than a slow way to
    give everyone every permission. Approving does NOT change what somebody may
    do; it authorises ONE act, on ONE subject, once, within a window. The row
    is consumed the moment it is used, and `used_at` records that it was.

    A standing grant would be indistinguishable from a role change made without
    anybody deciding to change a role.

    ── SOME THINGS ARE NOT REQUESTABLE, AND THAT LIST IS THE POINT ─────────────

    See DELEGABLE in operations/approvals.py. Changing a staff role, inviting
    staff, deactivating an account and editing the company's billing details
    are absent from it deliberately. Those are the acts that GRANT power or
    REDIRECT MONEY, and a request to perform one is exactly what an attacker
    with a delivery account would send — reasonable-looking, urgent, arriving
    while the founder is on a phone. One distracted approval on "make me a
    founder" is the entire permission model.

    For those, the answer stays "the founder does it", and the refusal says so.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Waiting on a founder"
        APPROVED = "approved", "Approved"
        DECLINED = "declined", "Declined"
        # The founder did it themselves rather than handing the permission
        # over. Kept distinct from APPROVED because "I did this for you" and
        # "you may do this" are different decisions and the log should not
        # blur them.
        DONE_BY_FOUNDER = "done", "Done by a founder"
        WITHDRAWN = "withdrawn", "Withdrawn"

    # How long an approval stays usable. Short on purpose: this exists to
    # unblock somebody who is working right now, and an approval still valid
    # next week is a permission nobody remembers granting.
    LIFETIME = timedelta(hours=8)

    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="access_requests",
        limit_choices_to={"is_staff": True},
    )
    requested_by_label = models.CharField(max_length=200, blank=True)

    # A key from operations/approvals.py — "client.archive", "offer.send".
    action = models.CharField(max_length=60, db_index=True)
    # What it is about: a reference, a client name. Part of what an approval
    # authorises, so that "yes, archive Kilimani Dental" cannot be spent on a
    # different client.
    subject = models.CharField(max_length=120, blank=True)

    reason = models.TextField(
        help_text=(
            "Why they need it. Required — this is the entire content of the "
            "decision, and a request without one asks the founder to approve "
            "a verb."
        )
    )

    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True
    )

    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="access_decisions",
        limit_choices_to={"is_staff": True},
    )
    decided_by_label = models.CharField(max_length=200, blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.TextField(blank=True)

    # Set when approved; the approval is dead after this.
    expires_at = models.DateTimeField(null=True, blank=True)
    # Set the moment the approval is spent. One act, once.
    used_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["status", "-created_at"], name="access_req_status_idx"),
            models.Index(
                fields=["requested_by", "action", "status"], name="access_req_lookup_idx"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.requested_by_label} → {self.action} ({self.status})"

    def is_live(self, now=None) -> bool:
        """Approved, unspent, and still inside its window."""
        if self.status != self.Status.APPROVED or self.used_at is not None:
            return False
        if self.expires_at is None:
            return False
        return self.expires_at > (now or timezone.now())

    @property
    def is_open(self) -> bool:
        return self.status == self.Status.PENDING



class OrderSeen(models.Model):
    """
    When this person last looked at this order.

    ── PER PERSON, NOT PER ORGANISATION ────────────────────────────────────────

    Two people at a client can both have accounts, and one of them opening an
    order says nothing about whether the other has seen it. A shared "seen"
    flag would clear the marker for a colleague who never looked — which is
    worse than no marker, because it produces a client who was never told and a
    system that believes they were.

    ── AND IT IS NOT AN ANALYTICS ROW ──────────────────────────────────────────

    One timestamp, overwritten. No view counts, no history of when somebody
    opened what. That would be a record of a client's reading habits, which is
    not ours to keep and is not what this is for.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="orders_seen"
    )
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="seen_by")
    seen_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("user", "order")]
        indexes = [models.Index(fields=["user", "order"], name="order_seen_idx")]

    def __str__(self) -> str:
        return f"{self.user_id} saw {self.order_id} at {self.seen_at:%Y-%m-%d %H:%M}"
