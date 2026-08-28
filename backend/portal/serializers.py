"""
Serializers for the client-facing dashboard.

All read-only. Nothing a client sends can create or change an order — orders
come from a signed SOW, not a form (Charter 02 §I).

What is exposed is decided by Charter 05, the Client Charter: the promise names
scope, exclusions, a weekly progress note, milestones, and a named contact. The
dashboard shows exactly that and no internal state beyond it.
"""

from __future__ import annotations

from rest_framework import serializers

from .models import Milestone, Order, ProgressNote


class ContactSerializer(serializers.Serializer):
    """
    Charter 05 §I — "a named point of contact".

    Name and email only. A staff member's account flags, last login and internal
    identifiers are none of the client's business, and a ModelSerializer over
    User would leak them the moment someone adds a field.
    """

    full_name = serializers.CharField()
    email = serializers.EmailField()


class ProgressNoteSerializer(serializers.ModelSerializer):
    author = serializers.CharField(source="author.full_name", read_only=True)

    class Meta:
        model = ProgressNote
        fields = ["week_of", "body", "author", "published_at"]
        read_only_fields = fields


class MilestoneSerializer(serializers.ModelSerializer):
    status_label = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = Milestone
        # amount_kes is a Decimal and is serialised as a STRING, which is DRF's
        # default and the right one: JSON numbers are floats, and money that has
        # been through a float is money you cannot reconcile.
        fields = ["name", "amount_kes", "due_on", "status", "status_label"]
        read_only_fields = fields


class OrderListSerializer(serializers.ModelSerializer):
    status_label = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = Order
        fields = ["reference", "title", "status", "status_label", "target_date"]
        read_only_fields = fields


class OrderDetailSerializer(serializers.ModelSerializer):
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    contact = ContactSerializer(read_only=True)
    organisation = serializers.CharField(source="organisation.name", read_only=True)
    notes = serializers.SerializerMethodField()
    milestones = MilestoneSerializer(many=True, read_only=True)

    class Meta:
        model = Order
        fields = [
            "reference",
            "title",
            "organisation",
            "scope",
            # Exclusions are shown as prominently as scope. Charter 05 §I puts
            # them in the promise for a reason: "anything not in the written
            # scope is a change request", and a client can only hold us to that
            # if they can see what was excluded.
            "exclusions",
            "status",
            "status_label",
            "contact",
            "started_on",
            "target_date",
            "notes",
            "milestones",
        ]
        read_only_fields = fields

    def get_notes(self, order: Order):
        """
        Published notes only, newest first.

        A draft is not a promise — the client is entitled to rely on what they
        were actually told, and nothing else. Scoped in selectors.py so the rule
        is not restated per view.
        """
        from .selectors import published_notes_for

        return ProgressNoteSerializer(published_notes_for(order), many=True).data


class OnboardingSerializer(serializers.Serializer):
    """
    What onboarding accepts. Note what it does NOT accept.

    No status, no order, no reference, no amount — nothing that would let a
    client create or influence an engagement. Charter 02 §I gives qualification
    to the commercial partners and the capacity veto to the founder; a form that
    could produce an Order would route around both. This produces an Enquiry,
    and an Enquiry is a request to be considered.
    """

    full_name = serializers.CharField(max_length=200)
    organisation_name = serializers.CharField(max_length=200)

    # The Playbook's qualification questions. Only the first is required: a
    # prospect who does not yet know their budget still has a real problem, and
    # making them invent a number to get past a form teaches them to lie to us.
    problem = serializers.CharField()
    monthly_cost = serializers.CharField(max_length=200, allow_blank=True, default="")
    timeline = serializers.CharField(max_length=100, allow_blank=True, default="")
    budget_range = serializers.CharField(max_length=100, allow_blank=True, default="")

    def validate_organisation_name(self, value: str) -> str:
        name = value.strip()
        if not name:
            raise serializers.ValidationError("Please give your organisation a name.")
        return name

    def validate_problem(self, value: str) -> str:
        problem = value.strip()
        # Not a length gate for its own sake: this text is what the commercial
        # partners qualify against, and "hi" gives them nothing to work with.
        if len(problem) < 20:
            raise serializers.ValidationError(
                "Tell us a little more — a sentence or two about what is going wrong."
            )
        return problem
