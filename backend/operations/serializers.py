"""
Serializers for the operations API.

Staff-facing, so these carry fields the client serializers deliberately do not:
draft notes, who decided what, the enquiry behind an order. None of it may leak
into `portal/serializers.py`, which is the client's view of the same rows.
"""

from __future__ import annotations

from django.utils import timezone
from rest_framework import serializers

from accounts.models import Membership, Organisation, User
from portal.models import (
    AccessRequest,
    ActivityLog,
    Blocker,
    ChangeRequest,
    ClientProfile,
    ContactAttachment,
    ContactLogEntry,
    Contract,
    Decision,
    DeliveryGate,
    HostingArrangement,
    Enquiry,
    Incident,
    Invoice,
    Milestone,
    Notification,
    Offer,
    Order,
    PaymentRecord,
    ProgressNote,
    SecurityCheck,
    Service,
    ServiceTier,
    Shift,
    System,
    SystemEvent,
    SupportMessage,
    SupportTicket,
    SystemKey,
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
    # Only read when the client has nothing on file at all — see
    # services.issue_direct_invoice. It says where the work came from and is
    # kept with the invoice in the log.
    outside_system = serializers.CharField(
        max_length=300, required=False, allow_blank=True, default=""
    )


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

    is_archived = serializers.BooleanField(read_only=True)

    class Meta:
        model = Organisation
        fields = [
            "id",
            "name",
            "created_at",
            "memberships",
            "order_count",
            "is_archived",
            "archived_at",
            "archived_reason",
        ]


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
            # The proposal. All optional — a renewal quoted in one line renders
            # as one line, and the document omits the headings nobody used.
            "context", "approach", "inclusions", "exclusions", "timeline",
            "payment_terms", "next_step",
        ]
        read_only_fields = fields

    def get_expired(self, offer: Offer) -> bool:
        return offer.is_expired()

    def get_created_by_name(self, offer: Offer) -> str:
        who = offer.created_by
        return (who.full_name or who.email) if who else ""


class OfferWriteSerializer(serializers.Serializer):
    """
    Drafting a quote or a proposal — the same row, filled in to different
    depths. See Offer in portal/models.py for why there is no second model.
    """

    organisation = serializers.IntegerField()
    title = serializers.CharField(max_length=200)
    detail = serializers.CharField(allow_blank=True, required=False, default="")
    amount_kes = serializers.DecimalField(max_digits=12, decimal_places=2)
    expires_on = serializers.DateField()
    tier = serializers.IntegerField(required=False, allow_null=True, default=None)

    # ── the proposal, all optional ──────────────────────────────────────────
    context = serializers.CharField(required=False, allow_blank=True, default="")
    approach = serializers.CharField(required=False, allow_blank=True, default="")
    inclusions = serializers.CharField(required=False, allow_blank=True, default="")
    exclusions = serializers.CharField(required=False, allow_blank=True, default="")
    timeline = serializers.CharField(
        max_length=300, required=False, allow_blank=True, default=""
    )
    payment_terms = serializers.CharField(required=False, allow_blank=True, default="")
    next_step = serializers.CharField(
        max_length=300, required=False, allow_blank=True, default=""
    )


class OfferReviseSerializer(OfferWriteSerializer):
    """
    Editing a DRAFT. The organisation cannot move — an offer that changed which
    client it was for would carry a reference somebody had already been quoted.
    """

    organisation = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields.pop("organisation", None)


class TaskSerializer(serializers.ModelSerializer):
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    priority_label = serializers.CharField(source="get_priority_display", read_only=True)
    assignee_name = serializers.SerializerMethodField()
    assignee_email = serializers.CharField(source="assignee.email", read_only=True)
    order_reference = serializers.CharField(
        source="order.reference", read_only=True, default=None
    )
    organisation_name = serializers.CharField(
        source="organisation.name", read_only=True, default=None
    )
    ticket_reference = serializers.CharField(
        source="ticket.reference", read_only=True, default=None
    )
    decision_reference = serializers.CharField(
        source="decision.reference", read_only=True, default=None
    )
    # Where it came from. Six weeks on, "why am I doing this" is answered by
    # the call it came out of.
    contact_summary = serializers.CharField(
        source="contact.summary", read_only=True, default=None
    )
    overdue = serializers.SerializerMethodField()

    class Meta:
        model = Task
        fields = [
            "id", "title", "detail", "assignee", "assignee_name", "assignee_email",
            "status", "status_label", "priority", "priority_label",
            "due_on", "overdue", "blocked_reason", "done_at",
            "order", "order_reference", "created_at",
            # What the work is about. All optional — a board that demands to
            # know which project a task belongs to is a board people stop
            # using.
            "organisation", "organisation_name",
            "ticket", "ticket_reference",
            "decision", "decision_reference",
            "contact", "contact_summary",
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
    # What it is about. All optional, and the client is inferred from whichever
    # of these is given — see services.assign_task.
    order = serializers.CharField(required=False, allow_blank=True, default="")
    organisation = serializers.IntegerField(required=False, allow_null=True, default=None)
    ticket = serializers.CharField(required=False, allow_blank=True, default="")
    decision = serializers.IntegerField(required=False, allow_null=True, default=None)
    # A logged conversation this work came out of. Picking one carries its
    # client and its order across, so nobody retypes a reference already
    # recorded against the call.
    contact = serializers.IntegerField(required=False, allow_null=True, default=None)
    due_on = serializers.DateField(required=False, allow_null=True, default=None)
    priority = serializers.ChoiceField(
        choices=Task.Priority.choices, required=False, default=Task.Priority.NORMAL
    )


class TaskStatusSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=Task.Status.choices)
    blocked_reason = serializers.CharField(
        required=False, allow_blank=True, default=""
    )


class SystemSerializer(serializers.ModelSerializer):
    kind_label = serializers.CharField(source="get_kind_display", read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    criticality_label = serializers.CharField(
        source="get_criticality_display", read_only=True
    )
    health_label = serializers.CharField(source="get_health_display", read_only=True)
    owner_name = serializers.SerializerMethodField()
    organisation_name = serializers.CharField(
        source="organisation.name", read_only=True, default=None
    )
    is_watched = serializers.BooleanField(read_only=True)
    heartbeat_stale = serializers.SerializerMethodField()
    active_keys = serializers.SerializerMethodField()
    # The highest tier where every published requirement is satisfied, and
    # whether a LIVE system sits below the bar the website states for going
    # live. Both computed — see System.security_tier_met.
    security_tier = serializers.SerializerMethodField()
    fails_tier_one = serializers.SerializerMethodField()
    security_assessed = serializers.SerializerMethodField()

    class Meta:
        model = System
        fields = [
            "id", "name", "slug", "kind", "kind_label",
            "status", "status_label", "criticality", "criticality_label",
            "purpose", "impact_if_down",
            "owner", "owner_name", "organisation", "organisation_name",
            "url", "health_url", "repository", "runbook",
            "health", "health_label", "health_detail", "checked_at",
            "heartbeat_at", "heartbeat_stale", "version",
            "is_watched", "active_keys", "created_at",
            "security_tier", "fails_tier_one", "security_assessed",
        ]
        read_only_fields = fields

    def get_owner_name(self, system: System) -> str:
        return system.owner.full_name or system.owner.email

    def get_heartbeat_stale(self, system: System) -> bool:
        return system.heartbeat_is_stale()

    def get_active_keys(self, system: System) -> int:
        return system.keys.filter(revoked_at__isnull=True).count()

    def get_security_tier(self, system: System) -> str | None:
        return system.security_tier_met()

    def get_fails_tier_one(self, system: System) -> bool:
        return system.fails_tier_one()

    def get_security_assessed(self, system: System) -> bool:
        """Distinct from passing. Never assessed shows as unassessed, not green."""
        return system.security_checks.exists()


class SystemWriteSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=120)
    slug = serializers.SlugField(max_length=120)
    kind = serializers.ChoiceField(choices=System.Kind.choices)
    criticality = serializers.ChoiceField(choices=System.Criticality.choices)
    purpose = serializers.CharField(max_length=300)
    impact_if_down = serializers.CharField(max_length=300)
    owner = serializers.IntegerField()
    organisation = serializers.IntegerField(
        required=False, allow_null=True, default=None
    )
    url = serializers.CharField(required=False, allow_blank=True, default="")
    health_url = serializers.CharField(required=False, allow_blank=True, default="")
    repository = serializers.CharField(required=False, allow_blank=True, default="")
    runbook = serializers.CharField(required=False, allow_blank=True, default="")
    status = serializers.ChoiceField(
        choices=System.Status.choices, required=False, default=System.Status.LIVE
    )


class SystemEventSerializer(serializers.ModelSerializer):
    level_label = serializers.CharField(source="get_level_display", read_only=True)
    system_slug = serializers.CharField(source="system.slug", read_only=True)
    system_name = serializers.CharField(source="system.name", read_only=True)

    class Meta:
        model = SystemEvent
        fields = [
            "id", "system_slug", "system_name", "level", "level_label",
            "message", "detail", "occurred_at", "received_at",
        ]
        read_only_fields = fields


class SystemKeySerializer(serializers.ModelSerializer):
    """The key's metadata. The token itself is not here and cannot be."""

    class Meta:
        model = SystemKey
        fields = ["id", "label", "prefix", "created_at", "last_used_at", "revoked_at"]
        read_only_fields = fields


class TicketMessageSerializer(serializers.ModelSerializer):
    """One message as OPERATIONS sees it — internal notes included."""

    class Meta:
        model = SupportMessage
        fields = [
            "id", "author_label", "from_staff", "internal", "body", "created_at"
        ]
        read_only_fields = fields


class TicketSerializer(serializers.ModelSerializer):
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    priority_label = serializers.CharField(source="get_priority_display", read_only=True)
    organisation_name = serializers.CharField(source="organisation.name", read_only=True)
    raised_by_name = serializers.SerializerMethodField()
    assigned_to_name = serializers.SerializerMethodField()
    order_reference = serializers.CharField(
        source="order.reference", read_only=True, default=None
    )
    messages = TicketMessageSerializer(many=True, read_only=True)
    # Seconds they waited the first time. Measured, never promised — see the
    # SupportTicket docstring.
    first_answer_seconds = serializers.SerializerMethodField()

    class Meta:
        model = SupportTicket
        fields = [
            "id", "reference", "subject", "status", "status_label",
            "priority", "priority_label",
            "organisation", "organisation_name", "raised_by_name",
            "assigned_to", "assigned_to_name", "order_reference",
            "created_at", "first_answered_at", "first_answer_seconds",
            "resolved_at", "messages",
        ]
        read_only_fields = fields

    def get_raised_by_name(self, ticket: SupportTicket) -> str:
        who = ticket.raised_by
        return (who.full_name or who.email) if who else ""

    def get_assigned_to_name(self, ticket: SupportTicket) -> str:
        who = ticket.assigned_to
        return (who.full_name or who.email) if who else ""

    def get_first_answer_seconds(self, ticket: SupportTicket) -> int | None:
        waited = ticket.waited_for_first_answer()
        return int(waited.total_seconds()) if waited is not None else None


class TicketReplySerializer(serializers.Serializer):
    body = serializers.CharField()
    internal = serializers.BooleanField(required=False, default=False)


class TicketStateSerializer(serializers.Serializer):
    status = serializers.ChoiceField(
        choices=SupportTicket.Status.choices, required=False, allow_null=True,
        default=None,
    )
    priority = serializers.ChoiceField(
        choices=SupportTicket.Priority.choices, required=False, allow_null=True,
        default=None,
    )
    assigned_to = serializers.IntegerField(required=False, allow_null=True, default=None)


class SecurityCheckSerializer(serializers.ModelSerializer):
    tier_label = serializers.CharField(source="get_tier_display", read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    assessed_by_name = serializers.SerializerMethodField()
    needs_a_note = serializers.BooleanField(read_only=True)

    class Meta:
        model = SecurityCheck
        fields = [
            "id", "tier", "tier_label", "item", "position",
            "status", "status_label", "note",
            "assessed_by_name", "assessed_at", "needs_a_note",
        ]
        read_only_fields = fields

    def get_assessed_by_name(self, check: SecurityCheck) -> str:
        who = check.assessed_by
        return (who.full_name or who.email) if who else ""


class SecurityCheckWriteSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=SecurityCheck.Status.choices)
    note = serializers.CharField(required=False, allow_blank=True, default="")



class BillingProfileSerializer(serializers.Serializer):
    """
    The company's own billing identity, as the settings form sees it.

    ── STORED AND EFFECTIVE ARE BOTH SENT ─────────────────────────────────────

    `stored` is exactly what is in the database, so the form shows blanks as
    blanks and never puts a fallback value into a field somebody is about to
    save — which would silently copy a setting into the database and make an
    env var look like a typed one.

    `effective` is what an invoice would actually print today, after the
    BILLING_* fallback. The form shows it beside each empty field, so "this is
    blank but invoices still say Genmars Tech Limited" is visible rather than
    something the founder discovers by issuing one.
    """

    legal_name = serializers.CharField(max_length=200, required=False, allow_blank=True)
    email = serializers.EmailField(required=False, allow_blank=True)
    kra_pin = serializers.CharField(max_length=20, required=False, allow_blank=True)
    postal_address = serializers.CharField(
        max_length=300, required=False, allow_blank=True
    )
    mpesa_paybill = serializers.CharField(
        max_length=20, required=False, allow_blank=True
    )
    mpesa_account_hint = serializers.CharField(
        max_length=60, required=False, allow_blank=True
    )
    bank_details = serializers.CharField(required=False, allow_blank=True)
    terms = serializers.CharField(required=False, allow_blank=True)

    # Shape only — lengths and types. What a paybill has to LOOK like, and why
    # an account hint must carry {number}, live in services.set_billing_details
    # with the rest of the rules, so the refusal comes back as {detail, field}
    # like every other operations refusal rather than as DRF's field-keyed
    # shape that this app's error handling does not read.


# ── the workroom ─────────────────────────────────────────────────────────────


class ShiftSerializer(serializers.ModelSerializer):
    """One clocked stretch, as a timesheet row."""

    person = serializers.SerializerMethodField()
    minutes = serializers.IntegerField(read_only=True)
    on_date = serializers.DateField(source="local_date", read_only=True)
    is_open = serializers.BooleanField(read_only=True)

    class Meta:
        model = Shift
        fields = [
            "id",
            "person",
            "started_at",
            "ended_at",
            "started_note",
            "ended_note",
            "ended_late",
            "minutes",
            "on_date",
            "is_open",
        ]

    def get_person(self, shift: Shift) -> dict:
        return {
            "id": shift.person_id,
            "name": shift.person.full_name or shift.person.email,
        }


class ClockSerializer(serializers.Serializer):
    """
    Clocking in or out.

    NOTE there is no `person`. The service takes the actor and nothing else —
    see Shift's docstring on why nobody clocks anybody else.
    """

    action = serializers.ChoiceField(choices=["in", "out"])
    note = serializers.CharField(max_length=200, required=False, allow_blank=True, default="")
    # Only read when closing a shift somebody forgot about, and only then. See
    # services.clock_out.
    ended_at = serializers.DateTimeField(required=False, allow_null=True)


class DecisionSerializer(serializers.ModelSerializer):
    """
    A register entry, whole. There is no summary form.

    The context and the alternatives are the parts worth reading, and a list
    view that showed only the title would be a list of assertions — which is
    the artefact this register exists to replace.
    """

    decided_by = serializers.SerializerMethodField()
    supersedes = serializers.SerializerMethodField()
    superseded_by = serializers.SerializerMethodField()
    status_label = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = Decision
        fields = [
            "id",
            "reference",
            "title",
            "context",
            "options",
            "decision",
            "consequences",
            "revisit_when",
            "status",
            "status_label",
            "decided_by",
            "decided_on",
            "supersedes",
            "superseded_by",
            "reversal_reason",
            "created_at",
        ]

    def get_decided_by(self, entry: Decision) -> str:
        # The label, not the FK. It outlives the account being deactivated,
        # which is the whole reason the column exists.
        return entry.decided_by_label

    def get_supersedes(self, entry: Decision) -> dict | None:
        if entry.supersedes is None:
            return None
        return {"reference": entry.supersedes.reference, "title": entry.supersedes.title}

    def get_superseded_by(self, entry: Decision) -> dict | None:
        replacement = entry.superseded_by.first()
        if replacement is None:
            return None
        return {"reference": replacement.reference, "title": replacement.title}


class DecisionWriteSerializer(serializers.Serializer):
    """
    Shape only. Every rule about what a decision must contain lives in
    services.record_decision, so refusals come back as {detail, field} like the
    rest of this API.
    """

    title = serializers.CharField(max_length=200, allow_blank=True)
    context = serializers.CharField(allow_blank=True)
    decision = serializers.CharField(allow_blank=True)
    options = serializers.CharField(required=False, allow_blank=True, default="")
    consequences = serializers.CharField(required=False, allow_blank=True, default="")
    revisit_when = serializers.CharField(
        max_length=300, required=False, allow_blank=True, default=""
    )
    status = serializers.ChoiceField(
        choices=[Decision.Status.PROPOSED, Decision.Status.DECIDED],
        required=False,
        default=Decision.Status.DECIDED,
    )
    decided_on = serializers.DateField(required=False, allow_null=True)
    supersedes = serializers.IntegerField(required=False, allow_null=True)


class DecisionActionSerializer(serializers.Serializer):
    """What can happen to an entry that already exists."""

    action = serializers.ChoiceField(choices=["decide", "reverse", "revise"])
    reason = serializers.CharField(required=False, allow_blank=True, default="")
    decided_on = serializers.DateField(required=False, allow_null=True)

    title = serializers.CharField(max_length=200, required=False, allow_blank=True)
    context = serializers.CharField(required=False, allow_blank=True)
    decision = serializers.CharField(required=False, allow_blank=True)
    options = serializers.CharField(required=False, allow_blank=True)
    consequences = serializers.CharField(required=False, allow_blank=True)
    revisit_when = serializers.CharField(max_length=300, required=False, allow_blank=True)


# ── the client record ────────────────────────────────────────────────────────


class ClientProfileSerializer(serializers.ModelSerializer):
    channel_label = serializers.CharField(
        source="get_preferred_channel_display", read_only=True
    )

    class Meta:
        model = ClientProfile
        fields = [
            "what_they_do",
            "website",
            "contact_name",
            "contact_role",
            "contact_phone",
            "contact_email",
            "preferred_channel",
            "channel_label",
            "client_since",
            "notes",
            "may_be_named",
            "permission_note",
            "updated_at",
        ]


class ClientProfileWriteSerializer(serializers.Serializer):
    """
    Shape only. The rule that turning on `may_be_named` requires the evidence
    to be named lives in services.set_client_profile, with the rest of the
    refusals that come back as {detail, field}.
    """

    what_they_do = serializers.CharField(max_length=200, required=False, allow_blank=True)
    website = serializers.URLField(required=False, allow_blank=True)
    contact_name = serializers.CharField(max_length=200, required=False, allow_blank=True)
    contact_role = serializers.CharField(max_length=120, required=False, allow_blank=True)
    contact_phone = serializers.CharField(max_length=40, required=False, allow_blank=True)
    contact_email = serializers.EmailField(required=False, allow_blank=True)
    preferred_channel = serializers.ChoiceField(
        choices=ClientProfile.Channel.choices, required=False, allow_blank=True
    )
    client_since = serializers.DateField(required=False, allow_null=True)
    notes = serializers.CharField(required=False, allow_blank=True)
    may_be_named = serializers.BooleanField(required=False)
    permission_note = serializers.CharField(
        max_length=300, required=False, allow_blank=True
    )


class HostingSerializer(serializers.ModelSerializer):
    kind_label = serializers.CharField(source="get_kind_display", read_only=True)
    holder_label = serializers.CharField(source="get_account_holder_display", read_only=True)
    system_slug = serializers.CharField(source="system.slug", read_only=True, default=None)
    days_until_renewal = serializers.SerializerMethodField()
    is_live = serializers.BooleanField(read_only=True)

    class Meta:
        model = HostingArrangement
        fields = [
            "id",
            "kind",
            "kind_label",
            "identifier",
            "provider",
            "account_holder",
            "holder_label",
            "renews_on",
            "days_until_renewal",
            "auto_renew",
            "annual_cost_kes",
            "annual_charge_kes",
            "notes",
            "system_slug",
            "is_live",
            "retired_at",
        ]

    def get_days_until_renewal(self, arrangement: HostingArrangement) -> int | None:
        return arrangement.days_until_renewal()


class HostingWriteSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=HostingArrangement.Kind.choices)
    identifier = serializers.CharField(max_length=200, allow_blank=True)
    provider = serializers.CharField(max_length=120, required=False, allow_blank=True)
    account_holder = serializers.ChoiceField(
        choices=HostingArrangement.Holder.choices, required=False
    )
    renews_on = serializers.DateField(required=False, allow_null=True)
    auto_renew = serializers.BooleanField(required=False, default=False)
    annual_cost_kes = serializers.DecimalField(
        max_digits=10, decimal_places=2, required=False, allow_null=True
    )
    annual_charge_kes = serializers.DecimalField(
        max_digits=10, decimal_places=2, required=False, allow_null=True
    )
    notes = serializers.CharField(required=False, allow_blank=True)


class AttachmentSerializer(serializers.ModelSerializer):
    """
    Metadata only. The bytes leave through AttachmentDownloadView and nowhere
    else — there is no URL here that a browser could render inline.
    """

    uploaded_by = serializers.CharField(source="uploaded_by_label", read_only=True)
    is_image = serializers.BooleanField(read_only=True)
    url = serializers.SerializerMethodField()

    class Meta:
        model = ContactAttachment
        fields = [
            "id",
            "original_name",
            "content_type",
            "size_bytes",
            "caption",
            "uploaded_by",
            "is_image",
            "url",
            "created_at",
        ]

    def get_url(self, attachment: ContactAttachment) -> str:
        # The download route, not a media path. MEDIA_URL is empty on purpose.
        return f"/api/attachments/{attachment.pk}"


class ContactLogSerializer(serializers.ModelSerializer):
    channel_label = serializers.CharField(source="get_channel_display", read_only=True)
    direction_label = serializers.CharField(source="get_direction_display", read_only=True)
    recorded_by = serializers.CharField(source="recorded_by_label", read_only=True)
    order_reference = serializers.CharField(
        source="order.reference", read_only=True, default=None
    )
    organisation_name = serializers.CharField(
        source="organisation.name", read_only=True
    )
    organisation_id = serializers.IntegerField(read_only=True)
    is_owed = serializers.BooleanField(read_only=True)
    is_overdue = serializers.SerializerMethodField()
    attachments = AttachmentSerializer(many=True, read_only=True)

    class Meta:
        model = ContactLogEntry
        fields = [
            "id",
            "organisation_id",
            "organisation_name",
            "channel",
            "channel_label",
            "direction",
            "direction_label",
            "happened_at",
            "with_whom",
            "summary",
            "detail",
            "recorded_by",
            "order_reference",
            "follow_up",
            "follow_up_by",
            "cleared_at",
            "is_owed",
            "is_overdue",
            "attachments",
        ]

    def get_is_overdue(self, entry: ContactLogEntry) -> bool:
        return entry.is_overdue()


class ContactLogWriteSerializer(serializers.Serializer):
    channel = serializers.ChoiceField(choices=ContactLogEntry.Channel.choices)
    direction = serializers.ChoiceField(choices=ContactLogEntry.Direction.choices)
    # Defaults ON. A conversation with a promise, or about a specific order,
    # becomes work on the board — see services._task_from_contact for why not
    # every conversation does.
    create_task = serializers.BooleanField(required=False, default=True)
    summary = serializers.CharField(max_length=300, allow_blank=True)
    detail = serializers.CharField(required=False, allow_blank=True, default="")
    with_whom = serializers.CharField(max_length=200, required=False, allow_blank=True, default="")
    happened_at = serializers.DateTimeField(required=False, allow_null=True)
    order = serializers.CharField(required=False, allow_blank=True, default="")
    follow_up = serializers.CharField(max_length=300, required=False, allow_blank=True, default="")
    follow_up_by = serializers.DateField(required=False, allow_null=True)


class OrderCreateSerializer(serializers.Serializer):
    """
    Shape only. Whether an order may be opened, and what it must contain, lives
    in services.create_order.
    """

    title = serializers.CharField(max_length=200, allow_blank=True)
    scope = serializers.CharField(allow_blank=True)
    exclusions = serializers.CharField(required=False, allow_blank=True, default="")
    contact = serializers.IntegerField(required=False, allow_null=True)
    target_date = serializers.DateField(required=False, allow_null=True)
    # Shape of work, and when it actually happened. A start date in the past
    # or any completion date marks it as recorded after the fact — see
    # services.create_order.
    kind = serializers.CharField(required=False, allow_blank=True, default="project")
    started_on = serializers.DateField(required=False, allow_null=True)
    completed_on = serializers.DateField(required=False, allow_null=True)
    status = serializers.CharField(required=False, allow_blank=True, default="")
    service = serializers.IntegerField(required=False, allow_null=True)
    from_contact = serializers.IntegerField(required=False, allow_null=True)
    # Default TRUE. An order the client has not been shown is a scope written
    # down where the only person who can say it is wrong will never read it.
    tell_client = serializers.BooleanField(required=False, default=True)


# ── asking a founder ─────────────────────────────────────────────────────────


class AccessRequestSerializer(serializers.ModelSerializer):
    requested_by = serializers.CharField(source="requested_by_label", read_only=True)
    decided_by = serializers.CharField(source="decided_by_label", read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    what = serializers.SerializerMethodField()
    is_live = serializers.SerializerMethodField()
    mine = serializers.SerializerMethodField()

    class Meta:
        model = AccessRequest
        fields = [
            "id",
            "action",
            "what",
            "subject",
            "reason",
            "status",
            "status_label",
            "requested_by",
            "decided_by",
            "decided_at",
            "decision_note",
            "expires_at",
            "used_at",
            "is_live",
            "mine",
            "created_at",
        ]

    def get_what(self, entry: AccessRequest) -> dict:
        """
        What the act is, in words, plus how bad it would be.

        The founder deciding sees the consequence rather than a slug — "hides
        them from the working screens, deletes nothing and can be undone" is
        the fact the decision actually turns on.
        """
        from operations import approvals

        return approvals.describe(entry)

    def get_is_live(self, entry: AccessRequest) -> bool:
        return entry.is_live()

    def get_mine(self, entry: AccessRequest) -> bool:
        request = self.context.get("request")
        return bool(request and entry.requested_by_id == request.user.id)


class AccessRequestWriteSerializer(serializers.Serializer):
    action = serializers.CharField(max_length=60)
    subject = serializers.CharField(max_length=120, required=False, allow_blank=True, default="")
    reason = serializers.CharField(allow_blank=True)


class AccessDecisionSerializer(serializers.Serializer):
    # `approve` hands the permission over for one act; `do_it_myself` records
    # that the founder handled it instead. Two different answers to the same
    # request, and the log keeps them apart.
    decision = serializers.ChoiceField(
        choices=["approve", "decline", "do_it_myself", "withdraw"]
    )
    note = serializers.CharField(required=False, allow_blank=True, default="")


class ChangeRequestSerializer(serializers.ModelSerializer):
    """
    A change request as staff see it: everything, including who classified it.

    `waited_hours` is here rather than in the client serializer for a reason.
    It measures how long a request sat unclassified, and it is the number that
    says whether "classify before work starts" is a practice or a sentence in a
    document — an internal measurement of ourselves, not a fact about the
    client's request.
    """

    status_label = serializers.CharField(source="get_status_display", read_only=True)
    classification_label = serializers.SerializerMethodField()
    organisation_name = serializers.CharField(source="organisation.name", read_only=True)
    order_reference = serializers.CharField(source="order.reference", read_only=True)
    order_title = serializers.CharField(source="order.title", read_only=True)
    classified_by_label = serializers.SerializerMethodField()
    contact_summary = serializers.CharField(
        source="contact.summary", read_only=True, default=None
    )
    waited_hours = serializers.SerializerMethodField()

    class Meta:
        model = ChangeRequest
        fields = [
            "id", "reference", "summary", "detail",
            "status", "status_label",
            "classification", "classification_label", "classification_note",
            "cost_impact_kes", "timeline_impact_days", "risk_note",
            "organisation_name", "order_reference", "order_title",
            "raised_at", "raised_by_label", "contact_summary",
            "classified_at", "classified_by_label", "waited_hours",
            "decided_at", "decision_note", "closed_at",
        ]
        read_only_fields = fields

    def get_classification_label(self, change) -> str:
        return change.get_classification_display() if change.classification else ""

    def get_classified_by_label(self, change) -> str:
        who = change.classified_by
        return (who.full_name or who.email) if who else ""

    def get_waited_hours(self, change):
        """
        Hours between raised and classified — or, while it is still
        unclassified, hours it has been waiting SO FAR.

        Counting from now for an open one is the point: a request that has sat
        for three days should read as three days, not as null.
        """
        end = change.classified_at or timezone.now()
        return round((end - change.raised_at).total_seconds() / 3600, 1)


class ClassifyChangeSerializer(serializers.Serializer):
    classification = serializers.ChoiceField(
        choices=ChangeRequest.Classification.choices
    )
    note = serializers.CharField(allow_blank=True)
    cost_impact_kes = serializers.DecimalField(
        max_digits=12, decimal_places=2, required=False, allow_null=True
    )
    timeline_impact_days = serializers.IntegerField(required=False, allow_null=True)
    risk_note = serializers.CharField(required=False, allow_blank=True, default="")
