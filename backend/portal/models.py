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

from datetime import date
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

    started_on = models.DateField(null=True, blank=True)
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

