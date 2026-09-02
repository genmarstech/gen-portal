"""
Serializers for the operations API.

Staff-facing, so these carry fields the client serializers deliberately do not:
draft notes, who decided what, the enquiry behind an order. None of it may leak
into `portal/serializers.py`, which is the client's view of the same rows.
"""

from __future__ import annotations

from rest_framework import serializers

from accounts.models import Membership, Organisation, User
from portal.models import (
    ActivityLog,
    Blocker,
    Contract,
    DeliveryGate,
    Enquiry,
    Incident,
    Invoice,
    Milestone,
    Notification,
    Offer,
    Order,
    PaymentRecord,
    ProgressNote,
    Service,
    ServiceTier,
    Task,
)


class PersonSerializer(serializers.Serializer):
    """A person, as operations needs to see them. Email included: staff have to
    be able to reply to a client, and this surface is already staff-only."""

    full_name = serializers.CharField()
    email = serializers.EmailField()


class EnquiryListSerializer(serializers.ModelSerializer):
    organisation = serializers.CharField(source="organisation.name")
    submitted_by = PersonSerializer(read_only=True)
    status_label = serializers.CharField(source="get_status_display")
    converted_reference = serializers.CharField(
        source="converted_to.reference", default=None, read_only=True
    )
    waiting_days = serializers.SerializerMethodField()
    # What they clicked on genmars.co.ke. Blank when they came through the open
    # route, which is an ordinary state and not a gap.
    service_name = serializers.CharField(
        source="service.name", read_only=True, default=""
    )

    class Meta:
        model = Enquiry
        fields = [
            "id",
            "organisation",
            "submitted_by",
            "problem",
            "timeline",
            "budget_range",
            "service",
            "service_name",
            "tier",
            "status",
            "status_label",
            "converted_reference",
            "created_at",
            "waiting_days",
        ]

    def get_waiting_days(self, obj: Enquiry) -> int:
        """
        How long this has sat undecided.

        Computed here rather than in the browser so every surface agrees on it,
        and so it is in the API for anything that later wants to alert on it.
        Zero once decided — a converted enquiry is not still waiting.
        """
        from django.utils import timezone

        if obj.status in (Enquiry.Status.CONVERTED, Enquiry.Status.DECLINED):
            return 0
        return (timezone.now() - obj.created_at).days


class EnquiryDetailSerializer(EnquiryListSerializer):
    decided_by = PersonSerializer(read_only=True)

    class Meta(EnquiryListSerializer.Meta):
        fields = EnquiryListSerializer.Meta.fields + [
            "monthly_cost",
            "outcome_note",
            "decided_by",
            "decided_at",
        ]


class MilestoneSerializer(serializers.ModelSerializer):
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    # A STRING, not a float. Money through a float is money you cannot
    # reconcile — the same rule the client API already follows.
    amount_kes = serializers.DecimalField(max_digits=12, decimal_places=2, coerce_to_string=True)

    class Meta:
        model = Milestone
        fields = [
            "id",
            "name",
            "amount_kes",
            "due_on",
            "status",
            "status_label",
            "position",
            "paid_at",
        ]
        read_only_fields = ["paid_at"]


class ProgressNoteSerializer(serializers.ModelSerializer):
    author = PersonSerializer(read_only=True)
    is_published = serializers.BooleanField(read_only=True)

    class Meta:
        model = ProgressNote
        fields = ["id", "week_of", "body", "author", "published_at", "is_published"]
        read_only_fields = ["author", "published_at"]


# ── engineering delivery ─────────────────────────────────────────────────────


class DeliveryGateSerializer(serializers.ModelSerializer):
    met_by = PersonSerializer(read_only=True)
    is_met = serializers.BooleanField(read_only=True)

    class Meta:
        model = DeliveryGate
        # `label` rather than get_gate_display(): the stored wording is the
        # standard this order was held to, and the choice text may have moved on.
        fields = ["id", "gate", "label", "is_met", "met_at", "met_by", "note", "position"]


class BlockerSerializer(serializers.ModelSerializer):
    raised_by = PersonSerializer(read_only=True)
    waiting_on_label = serializers.CharField(source="get_waiting_on_display", read_only=True)
    is_open = serializers.BooleanField(read_only=True)
    age_days = serializers.IntegerField(read_only=True)

    class Meta:
        model = Blocker
        fields = [
            "id", "summary", "detail", "waiting_on", "waiting_on_label",
            "raised_by", "raised_at", "cleared_at", "resolution", "is_open", "age_days",
        ]
        read_only_fields = ["raised_by", "raised_at", "cleared_at"]


class GateWriteSerializer(serializers.Serializer):
    met = serializers.BooleanField()
    note = serializers.CharField(required=False, allow_blank=True, default="")


class BlockerWriteSerializer(serializers.Serializer):
    summary = serializers.CharField(max_length=200)
    detail = serializers.CharField(required=False, allow_blank=True, default="")
    waiting_on = serializers.ChoiceField(choices=Blocker.WaitingOn.choices)


# ── services and contracts ───────────────────────────────────────────────────


class ServiceSerializer(serializers.ModelSerializer):
    deliverable_list = serializers.ListField(child=serializers.CharField(), read_only=True)
    order_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Service
        fields = [
            "id", "name", "slug", "summary", "default_scope", "default_exclusions",
            "default_deliverables", "deliverable_list", "is_active", "order_count",
        ]


class ServiceWriteSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=120)
    summary = serializers.CharField(max_length=300, required=False, allow_blank=True, default="")
    default_scope = serializers.CharField(required=False, allow_blank=True, default="")
    default_exclusions = serializers.CharField(required=False, allow_blank=True, default="")
    default_deliverables = serializers.CharField(required=False, allow_blank=True, default="")
    is_active = serializers.BooleanField(required=False, default=True)


class ContractSerializer(serializers.ModelSerializer):
    reference = serializers.CharField(read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    issued_by = PersonSerializer(read_only=True)
    recorded_by = PersonSerializer(read_only=True)
    deliverable_list = serializers.ListField(child=serializers.CharField(), read_only=True)
    # A STRING. Money through a float is money you cannot reconcile — and this
    # is the number on a document somebody signed.
    total_kes = serializers.DecimalField(
        max_digits=12, decimal_places=2, coerce_to_string=True, read_only=True
    )

    class Meta:
        model = Contract
        fields = [
            "id", "version", "reference", "title", "scope", "exclusions",
            "deliverables", "deliverable_list", "total_kes", "payment_terms",
            "target_date", "status", "status_label", "issued_at", "issued_by",
            "signed_on", "signed_by_name", "signature_note", "recorded_by",
            "created_at",
        ]


class IssueContractSerializer(serializers.Serializer):
    deliverables = serializers.CharField(required=False, allow_blank=True, default="")


class SignatureSerializer(serializers.Serializer):
    signed_on = serializers.DateField()
    signed_by_name = serializers.CharField(max_length=200)
    note = serializers.CharField(required=False, allow_blank=True, default="")


class VoidSerializer(serializers.Serializer):
    reason = serializers.CharField()


class PaymentRecordSerializer(serializers.ModelSerializer):
    """One recorded payment, as operations sees it."""

    method_label = serializers.CharField(source="get_method_display", read_only=True)
    recorded_by_name = serializers.SerializerMethodField()

    class Meta:
        model = PaymentRecord
        fields = [
            "id", "method", "method_label", "reference", "amount_kes",
            "paid_on", "note", "recorded_by_name", "created_at",
        ]
        read_only_fields = fields

    def get_recorded_by_name(self, payment: PaymentRecord) -> str:
        who = payment.recorded_by
        if who is None:
            # The M-Pesa callback, not a person who claims to have seen it.
            return "M-Pesa callback" if payment.mpesa_payment_id else ""
        return who.full_name or who.email


class InvoiceSerializer(serializers.ModelSerializer):
    """
    An invoice, as operations sees it.

    `amount_kes` is a STRING — DRF's default for Decimal, and the right one.
    JSON numbers are IEEE floats and money that has been through a float is
    money you cannot reconcile against a bank statement.
    """

    status_label = serializers.CharField(source="get_status_display", read_only=True)
    milestone_name = serializers.CharField(source="milestone.name", read_only=True, default="")
    issued_by_name = serializers.SerializerMethodField()
    overdue = serializers.SerializerMethodField()
    organisation_name = serializers.CharField(
        source="organisation.name", read_only=True
    )
    order_reference = serializers.CharField(
        source="order.reference", read_only=True, default=None
    )
    amount_paid = serializers.DecimalField(
        max_digits=12, decimal_places=2, coerce_to_string=True, read_only=True
    )
    balance = serializers.DecimalField(
        max_digits=12, decimal_places=2, coerce_to_string=True, read_only=True
    )
    payments = PaymentRecordSerializer(many=True, read_only=True)

    class Meta:
        model = Invoice
        fields = [
            "id", "number", "description", "amount_kes", "status", "status_label",
            "issued_on", "due_on", "overdue", "paid_on", "payment_reference",
            "milestone", "milestone_name", "issued_by_name", "void_reason",
            "organisation", "organisation_name", "order_reference",
            "amount_paid", "balance", "payments",
        ]
        read_only_fields = fields

    def get_issued_by_name(self, invoice: Invoice) -> str:
        who = invoice.issued_by
        return (who.full_name or who.email) if who else ""

    def get_overdue(self, invoice: Invoice) -> bool:
        return invoice.is_overdue()


class InvoiceWriteSerializer(serializers.Serializer):
    """
    Issuing an invoice.

    Everything is optional because billing a milestone needs only the milestone
    — the description and amount are copied from it. services.issue_invoice
    decides what is actually required and refuses clearly; duplicating that
    here would mean two places to keep in step.
    """

    # `default=None` matters: without it the key is ABSENT from validated_data
    # when the caller omits it, and views.py reads it positionally. A KeyError
    # 500 on the ad-hoc-invoice path, which the service-level tests never saw
    # because they bypass the serializer.
    milestone = serializers.PrimaryKeyRelatedField(
        queryset=Milestone.objects.all(), required=False, allow_null=True, default=None
    )
    description = serializers.CharField(max_length=300, required=False, allow_blank=True, default="")
    amount_kes = serializers.DecimalField(
        max_digits=12, decimal_places=2, required=False, allow_null=True, default=None
    )
    due_on = serializers.DateField(required=False, allow_null=True, default=None)
    issued_on = serializers.DateField(required=False, allow_null=True, default=None)


class PaymentSerializer(serializers.Serializer):
    """
    One payment against an invoice. Several of these can settle one invoice.

    `amount_kes` is optional and defaults to the whole outstanding balance,
    because settling an invoice in one go is the common case and should stay a
    one-field form. Everything else about the arithmetic lives in
    services.record_payment.

    `reference` may be blank only for cash — the service enforces that, not
    this serializer, so the rule has one home.
    """

    method = serializers.ChoiceField(
        choices=PaymentRecord.Method.choices,
        required=False,
        default=PaymentRecord.Method.MPESA,
    )
    reference = serializers.CharField(
        max_length=64, required=False, allow_blank=True, default=""
    )
    amount_kes = serializers.DecimalField(
        max_digits=12, decimal_places=2, required=False, allow_null=True, default=None
    )
    paid_on = serializers.DateField(required=False, allow_null=True, default=None)
    note = serializers.CharField(
        max_length=200, required=False, allow_blank=True, default=""
    )


class DirectInvoiceSerializer(serializers.Serializer):
    """
    An invoice raised straight to a client, with no order behind it.

    No `milestone` field, and there never should be: a milestone belongs to an
    order, and the database refuses the combination anyway — see the check
    constraint on Invoice.
    """

    organisation = serializers.IntegerField()
    description = serializers.CharField(max_length=300)
    amount_kes = serializers.DecimalField(max_digits=12, decimal_places=2)
    due_on = serializers.DateField(required=False, allow_null=True, default=None)
    issued_on = serializers.DateField(required=False, allow_null=True, default=None)


class VoidInvoiceSerializer(serializers.Serializer):
    reason = serializers.CharField()


class OrderListSerializer(serializers.ModelSerializer):
    organisation = serializers.CharField(source="organisation.name")
    contact = PersonSerializer(read_only=True)
    status_label = serializers.CharField(source="get_status_display")

    class Meta:
        model = Order
        fields = [
            "reference",
            "title",
            "organisation",
            "contact",
            "status",
            "status_label",
            "started_on",
            "target_date",
            "created_at",
        ]


class OrderDetailSerializer(OrderListSerializer):
    notes = ProgressNoteSerializer(many=True, read_only=True)
    milestones = MilestoneSerializer(many=True, read_only=True)
    gates = DeliveryGateSerializer(many=True, read_only=True)
    blockers = BlockerSerializer(many=True, read_only=True)
    contracts = ContractSerializer(many=True, read_only=True)
    invoices = InvoiceSerializer(many=True, read_only=True)
    service = ServiceSerializer(read_only=True)
    enquiry_id = serializers.IntegerField(
        source="from_enquiry.id", default=None, read_only=True
    )

    class Meta(OrderListSerializer.Meta):
        fields = OrderListSerializer.Meta.fields + [
            "scope",
            "exclusions",
            "notes",
            "milestones",
            "gates",
            "blockers",
            "contracts",
            "invoices",
            "service",
            "enquiry_id",
        ]


class OrderWriteSerializer(serializers.ModelSerializer):
    """
    What staff may change on an existing order.

    `reference` and `organisation` are absent on purpose. A reference is how the
    client refers to the work in email and on an invoice, and moving an order
    between organisations would move a client's data to another client rather
    than correcting a mistake — that is a deletion and a re-creation, done
    deliberately.
    """

    class Meta:
        model = Order
        fields = [
            "title",
            "scope",
            "exclusions",
            "status",
            "started_on",
            "target_date",
            "contact",
            "service",
        ]

    def validate_contact(self, value):
        if value and not value.is_staff:
            raise serializers.ValidationError(
                "The named contact must be a Genmars account."
            )
        # Charter 05 §I promises "a named point of contact". A revoked account
        # cannot sign in, so naming one keeps the field filled while breaking
        # the promise it exists to keep — which is worse than leaving it empty,
        # because it looks answered.
        if value and not value.is_active:
            raise serializers.ValidationError(
                "That colleague's access has been revoked. Name someone who can "
                "still be reached."
            )
        return value


class ConvertSerializer(serializers.Serializer):
    """Turning an enquiry into an order. Scope is required; see services.py."""

    title = serializers.CharField(max_length=200)
    scope = serializers.CharField()
    exclusions = serializers.CharField(required=False, allow_blank=True, default="")
    target_date = serializers.DateField(required=False, allow_null=True)
    contact = serializers.IntegerField(required=False, allow_null=True)
    service = serializers.IntegerField(required=False, allow_null=True)


class DecideSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=Enquiry.Status.choices)
    note = serializers.CharField(required=False, allow_blank=True, default="")


# ── client accounts ──────────────────────────────────────────────────────────


class MembershipSerializer(serializers.ModelSerializer):
    user = PersonSerializer(read_only=True)
    role_label = serializers.CharField(source="get_role_display", read_only=True)
    invited_by = PersonSerializer(read_only=True)
    # Whether they have ever set a password. An invited account that was never
    # accepted looks identical to an active one in a members list, and the
    # difference is the whole point of an invite.
    accepted = serializers.SerializerMethodField()

    class Meta:
        model = Membership
        fields = [
            "id", "user", "role", "role_label", "receives_updates",
            "invited_by", "created_at", "accepted",
        ]

    def get_accepted(self, obj: Membership) -> bool:
        return obj.user.is_email_verified and obj.user.has_usable_password()


class OrganisationSerializer(serializers.ModelSerializer):
    memberships = MembershipSerializer(many=True, read_only=True)
    order_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Organisation
        fields = ["id", "name", "created_at", "memberships", "order_count"]


class OrganisationWriteSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=200)


class InviteSerializer(serializers.Serializer):
    email = serializers.EmailField(max_length=254)
    full_name = serializers.CharField(max_length=200, required=False, allow_blank=True, default="")
    role = serializers.ChoiceField(choices=Membership.Role.choices, default=Membership.Role.MEMBER)

    def validate_email(self, value: str) -> str:
        # Same normalisation as accounts/views.py — addresses are stored
        # lower-cased and a mixed-case invite would create a second account.
        return value.strip().lower()


class MembershipWriteSerializer(serializers.Serializer):
    role = serializers.ChoiceField(choices=Membership.Role.choices, required=False)
    receives_updates = serializers.BooleanField(required=False)


# ── the team ─────────────────────────────────────────────────────────────────


class TeamMemberSerializer(serializers.ModelSerializer):
    role_label = serializers.CharField(source="get_staff_role_display", read_only=True)
    # Whether they have ever set a password. An invited colleague who never
    # accepted cannot sign in, and in a list of three people that difference
    # is the whole state of the invitation.
    accepted = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id", "email", "full_name", "staff_role", "role_label",
            "is_active", "date_joined", "accepted",
        ]

    def get_accepted(self, obj: User) -> bool:
        return obj.is_email_verified and obj.has_usable_password()


class StaffInviteSerializer(serializers.Serializer):
    email = serializers.EmailField(max_length=254)
    full_name = serializers.CharField(max_length=200, required=False, allow_blank=True, default="")
    role = serializers.ChoiceField(choices=User.StaffRole.choices)

    def validate_email(self, value: str) -> str:
        return value.strip().lower()


class StaffWriteSerializer(serializers.Serializer):
    role = serializers.ChoiceField(choices=User.StaffRole.choices, required=False)
    is_active = serializers.BooleanField(required=False)


class NotificationSerializer(serializers.ModelSerializer):
    """A notification on the operations surface. Same shape as the client one."""

    read = serializers.BooleanField(source="is_read", read_only=True)

    class Meta:
        model = Notification
        fields = ["id", "kind", "title", "body", "url", "created_at", "read"]
        read_only_fields = fields


class IncidentSerializer(serializers.ModelSerializer):
    """An incident, as operations sees it."""

    severity_label = serializers.CharField(source="get_severity_display", read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    raised_by_name = serializers.SerializerMethodField()
    # Seconds, formatted by the client. The gap between starting and being
    # noticed is the number that says whether monitoring works.
    undetected_seconds = serializers.SerializerMethodField()
    has_post_mortem = serializers.BooleanField(read_only=True)
    needs_post_mortem = serializers.BooleanField(read_only=True)

    class Meta:
        model = Incident
        fields = [
            "id", "reference", "title", "severity", "severity_label",
            "status", "status_label",
            "started_at", "detected_at", "mitigated_at", "resolved_at",
            "undetected_seconds",
            "summary", "client_impact",
            "what_happened", "why", "prevention",
            "has_post_mortem", "needs_post_mortem",
            "raised_by_name", "created_at",
        ]
        read_only_fields = fields

    def get_raised_by_name(self, incident: Incident) -> str:
        who = incident.raised_by
        return (who.full_name or who.email) if who else ""

    def get_undetected_seconds(self, incident: Incident) -> int | None:
        gap = incident.undetected_for()
        return int(gap.total_seconds()) if gap is not None else None


class IncidentWriteSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=200)
    severity = serializers.ChoiceField(choices=Incident.Severity.choices)
    started_at = serializers.DateTimeField()
    detected_at = serializers.DateTimeField(
        required=False, allow_null=True, default=None
    )
    summary = serializers.CharField()
    client_impact = serializers.CharField(
        required=False, allow_blank=True, default=""
    )


class PostMortemSerializer(serializers.Serializer):
    """
    Every field optional: the parts are written as they become known.

    A post-mortem completed in one sitting on the day is a guess about the
    cause. Letting the parts land separately is what makes them true.
    """

    what_happened = serializers.CharField(
        required=False, allow_blank=True, default=None
    )
    why = serializers.CharField(required=False, allow_blank=True, default=None)
    prevention = serializers.CharField(
        required=False, allow_blank=True, default=None
    )


class TierSerializer(serializers.ModelSerializer):
    """A tier as operations sees it, including where it disagrees with the site."""

    price_kes = serializers.DecimalField(
        max_digits=12, decimal_places=2, coerce_to_string=True, read_only=True
    )
    published_price_kes = serializers.DecimalField(
        max_digits=12, decimal_places=2, coerce_to_string=True, read_only=True
    )
    differs_from_website = serializers.BooleanField(read_only=True)
    includes = serializers.ListField(source="included", read_only=True)
    service_slug = serializers.CharField(source="service.slug", read_only=True)
    service_name = serializers.CharField(source="service.name", read_only=True)
    price_unit = serializers.CharField(source="service.price_unit", read_only=True)

    class Meta:
        model = ServiceTier
        fields = [
            "id", "slug", "name", "price_kes", "published_price_kes",
            "differs_from_website", "is_from", "lead", "includes",
            "service_slug", "service_name", "price_unit", "position",
        ]
        read_only_fields = fields


class TierPriceSerializer(serializers.Serializer):
    price_kes = serializers.DecimalField(max_digits=12, decimal_places=2)
    is_from = serializers.BooleanField(required=False, allow_null=True, default=None)


class ActivitySerializer(serializers.ModelSerializer):
    action_label = serializers.CharField(source="get_action_display", read_only=True)
    organisation_name = serializers.CharField(
        source="organisation.name", read_only=True, default=None
    )

    class Meta:
        model = ActivityLog
        fields = [
            "id", "action", "action_label", "actor_label", "subject",
            "summary", "detail", "organisation_name", "created_at",
        ]
        read_only_fields = fields


class OfferSerializer(serializers.ModelSerializer):
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    organisation_name = serializers.CharField(source="organisation.name", read_only=True)
    amount_kes = serializers.DecimalField(
        max_digits=12, decimal_places=2, coerce_to_string=True, read_only=True
    )
    list_price_kes = serializers.DecimalField(
        max_digits=12, decimal_places=2, coerce_to_string=True, read_only=True
    )
    discount_kes = serializers.DecimalField(
        max_digits=12, decimal_places=2, coerce_to_string=True, read_only=True
    )
    expired = serializers.SerializerMethodField()
    created_by_name = serializers.SerializerMethodField()

    class Meta:
        model = Offer
        fields = [
            "id", "reference", "organisation", "organisation_name",
            "title", "detail", "tier_name",
            "amount_kes", "list_price_kes", "discount_kes",
            "status", "status_label", "expires_on", "expired",
            "sent_at", "decided_at", "decline_reason",
            "created_by_name", "created_at",
        ]
        read_only_fields = fields

    def get_expired(self, offer: Offer) -> bool:
        return offer.is_expired()

    def get_created_by_name(self, offer: Offer) -> str:
        who = offer.created_by
        return (who.full_name or who.email) if who else ""


class OfferWriteSerializer(serializers.Serializer):
    organisation = serializers.IntegerField()
    title = serializers.CharField(max_length=200)
    detail = serializers.CharField()
    amount_kes = serializers.DecimalField(max_digits=12, decimal_places=2)
    expires_on = serializers.DateField()
    tier = serializers.IntegerField(required=False, allow_null=True, default=None)


class TaskSerializer(serializers.ModelSerializer):
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    priority_label = serializers.CharField(source="get_priority_display", read_only=True)
    assignee_name = serializers.SerializerMethodField()
    assignee_email = serializers.CharField(source="assignee.email", read_only=True)
    order_reference = serializers.CharField(
        source="order.reference", read_only=True, default=None
    )
    overdue = serializers.SerializerMethodField()

    class Meta:
        model = Task
        fields = [
            "id", "title", "detail", "assignee", "assignee_name", "assignee_email",
            "status", "status_label", "priority", "priority_label",
            "due_on", "overdue", "blocked_reason", "done_at",
            "order", "order_reference", "created_at",
        ]
        read_only_fields = fields

    def get_assignee_name(self, task: Task) -> str:
        return task.assignee.full_name or task.assignee.email

    def get_overdue(self, task: Task) -> bool:
        return task.is_overdue()


class TaskWriteSerializer(serializers.Serializer):
    assignee = serializers.IntegerField()
    title = serializers.CharField(max_length=200)
    detail = serializers.CharField(required=False, allow_blank=True, default="")
    order = serializers.CharField(required=False, allow_blank=True, default="")
    due_on = serializers.DateField(required=False, allow_null=True, default=None)
    priority = serializers.ChoiceField(
        choices=Task.Priority.choices, required=False, default=Task.Priority.NORMAL
    )


class TaskStatusSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=Task.Status.choices)
    blocked_reason = serializers.CharField(
        required=False, allow_blank=True, default=""
    )

