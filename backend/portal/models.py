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
