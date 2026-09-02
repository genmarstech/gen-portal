"""
Serializers for the operations API.

Staff-facing, so these carry fields the client serializers deliberately do not:
draft notes, who decided what, the enquiry behind an order. None of it may leak
into `portal/serializers.py`, which is the client's view of the same rows.
"""

from __future__ import annotations

from rest_framework import serializers

from accounts.models import Membership, Organisation
from portal.models import Blocker, DeliveryGate, Enquiry, Milestone, Order, ProgressNote


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

    class Meta:
        model = Enquiry
        fields = [
            "id",
            "organisation",
            "submitted_by",
            "problem",
            "timeline",
            "budget_range",
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
        ]

    def validate_contact(self, value):
        if value and not value.is_staff:
            raise serializers.ValidationError(
                "The named contact must be a Genmars account."
            )
        return value


class ConvertSerializer(serializers.Serializer):
    """Turning an enquiry into an order. Scope is required; see services.py."""

    title = serializers.CharField(max_length=200)
    scope = serializers.CharField()
    exclusions = serializers.CharField(required=False, allow_blank=True, default="")
    target_date = serializers.DateField(required=False, allow_null=True)
    contact = serializers.IntegerField(required=False, allow_null=True)


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
