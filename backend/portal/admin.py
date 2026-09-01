"""
Admin for orders.

This is where an Order is created — after qualification and a signed SOW, by
staff. There is deliberately no client-facing endpoint that creates one
(Charter 02 §I: qualification belongs to the commercial partners, the capacity
veto to the founder).
"""

from django.contrib import admin

from .models import Enquiry, Milestone, Order, ProgressNote


class MilestoneInline(admin.TabularInline):
    model = Milestone
    extra = 0
    fields = ["position", "name", "amount_kes", "due_on", "status"]


class ProgressNoteInline(admin.TabularInline):
    """
    Notes are added here week by week.

    Leave `published_at` empty to draft one; the client sees nothing until it is
    set. Once published the body is immutable — a progress log that can be
    quietly rewritten is not a record. Corrections are a new note.
    """

    model = ProgressNote
    extra = 0
    fields = ["week_of", "body", "author", "published_at"]
    autocomplete_fields = ["author"]


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ["reference", "title", "organisation", "status", "target_date", "contact"]
    list_filter = ["status"]
    search_fields = ["reference", "title", "organisation__name"]
    autocomplete_fields = ["organisation", "contact"]
    inlines = [MilestoneInline, ProgressNoteInline]

    fieldsets = [
        (None, {"fields": ["organisation", "reference", "title", "status"]}),
        (
            "What was agreed",
            {
                "fields": ["scope", "exclusions"],
                "description": (
                    "Charter 05 §I — fixed scope and fixed price, with exclusions "
                    "stated IN WRITING before work begins. The client sees both, "
                    "so write exclusions as carefully as scope."
                ),
            },
        ),
        ("People and dates", {"fields": ["contact", "started_on", "target_date"]}),
    ]


@admin.register(Enquiry)
class EnquiryAdmin(admin.ModelAdmin):
    """
    What onboarding produces. NOT an order.

    Qualify it (Playbook §3), confirm capacity with the founder, and only then
    create an Order. Converting is a deliberate act, not a status flip.
    """

    list_display = [
        "organisation", "submitted_by", "status", "converted_to", "timeline",
        "created_at",
    ]
    list_filter = ["status"]
    search_fields = ["organisation__name", "submitted_by__email"]

    # The triage fields are written by the operations app, which enforces the
    # rules the admin cannot: an enquiry converts once, a decline needs a
    # reason, and CONVERTED is a side effect of creating an order rather than a
    # status anyone may pick. Editable here they are four ways to produce a
    # record that says a decision happened and cannot say what it decided.
    readonly_fields = [
        "organisation", "submitted_by", "created_at",
        "converted_to", "decided_by", "decided_at",
    ]
