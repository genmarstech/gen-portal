"""
Admin for accounts.

Staff work here in v1; there is no staff-facing UI yet. That is a deliberate
scope decision, not an oversight — the client-facing half is what a client
values, and Django's admin is genuinely adequate for the handful of orders
Stage 0 will carry.
"""

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import EmailCode, Membership, Organisation, User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    ordering = ["email"]
    list_display = ["email", "full_name", "is_staff", "email_verified_at", "is_locked"]
    list_filter = ["is_staff", "is_active"]
    search_fields = ["email", "full_name"]
    readonly_fields = ["date_joined", "last_login", "failed_sign_ins", "locked_until"]

    fieldsets = [
        (None, {"fields": ["email", "password"]}),
        ("Profile", {"fields": ["full_name"]}),
        ("Access", {"fields": ["is_active", "is_staff", "is_superuser", "groups"]}),
        ("Verification", {"fields": ["email_verified_at"]}),
        (
            "Security",
            {
                "fields": ["failed_sign_ins", "locked_until", "last_login", "date_joined"],
                "description": "Locks clear on their own. Clear locked_until to release one early.",
            },
        ),
    ]
    add_fieldsets = [
        (None, {"classes": ["wide"], "fields": ["email", "password1", "password2"]}),
    ]

    @admin.display(boolean=True, description="Locked")
    def is_locked(self, obj: User) -> bool:
        return obj.is_locked


class MembershipInline(admin.TabularInline):
    model = Membership
    extra = 1
    autocomplete_fields = ["user"]


@admin.register(Organisation)
class OrganisationAdmin(admin.ModelAdmin):
    list_display = ["name", "created_at"]
    search_fields = ["name"]
    inlines = [MembershipInline]


@admin.register(EmailCode)
class EmailCodeAdmin(admin.ModelAdmin):
    """
    Visible for debugging delivery, but the code itself is NOT here — only its
    hash, and that is not shown either. If someone cannot receive a code, issue
    a new one; never read one out of the database.
    """

    list_display = ["user", "purpose", "created_at", "expires_at", "used_at", "attempts"]
    list_filter = ["purpose"]
    search_fields = ["user__email"]
    readonly_fields = ["user", "purpose", "created_at", "expires_at", "used_at", "attempts"]

    def has_add_permission(self, request):
        return False

    def get_exclude(self, request, obj=None):
        return ["code_hash"]
