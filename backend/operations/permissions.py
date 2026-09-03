"""
Who may use the operations API.

═══════════════════════════════════════════════════════════════════════════════
EVERY view in this app is staff-only, and the gate is `is_staff`.

`is_staff` already means "Genmars, not client" — accounts/models.py says so and
the portal's own models rely on it (`Order.contact` and `ProgressNote.author`
are both `limit_choices_to={"is_staff": True}`). Reusing it is the whole point
of putting operations behind the accounts we already have: no second identity,
no second password, no second thing to get wrong.
═══════════════════════════════════════════════════════════════════════════════

WHY THIS IS NOT `portal/selectors.py`

The client side scopes every read through Membership and says plainly that
`is_staff` grants nothing there. That must stay true: a staff account browsing
the client portal sees exactly what its memberships allow, and nothing else.

This app is the opposite surface — it reads across every organisation on
purpose — so it gets its own permission and its own selectors rather than
loosening the client ones. Two surfaces, two choke points, neither able to
weaken the other by accident.
"""

from __future__ import annotations

from rest_framework.permissions import BasePermission


class IsStaff(BasePermission):
    """
    Authenticated AND `is_staff`.

    Both halves matter. `IsAuthenticated` alone would let any client account
    read every organisation's enquiries, which is the confidentiality breach
    Charter 05 §V exists to prevent — and it would fail silently, with the wrong
    data simply appearing on a page nobody thought to check.
    """

    message = "This is a Genmars staff area."

    def has_permission(self, request, view) -> bool:
        user = request.user
        return bool(user and user.is_authenticated and user.is_staff)


# ─────────────────────────────────────────────────────────────────────────────
# What a staff account may CHANGE
# ─────────────────────────────────────────────────────────────────────────────
#
# READ IS SHARED, WRITE IS SCOPED, and the split is deliberate. Three people
# hiding work from each other would be theatre. Deciding who may commit the
# company is not, and Charter 02 §I already decided it: qualification belongs
# to the commercial partners, the capacity veto and pricing to the founder.
#
# These read the properties on User rather than restating the rule, so the
# answer to "who may do this" lives in one place and the views only say which
# question they are asking.


class CanQualify(IsStaff):
    """
    Decide whether an enquiry becomes work.

    Charter 02 §I. A delivery engineer converting an enquiry would commit the
    company's capacity, which is the founder's veto and the partners' call.
    """

    message = "Qualifying an enquiry is a commercial decision."

    def has_permission(self, request, view) -> bool:
        return super().has_permission(request, view) and request.user.can_qualify


class CanCommit(IsStaff):
    """
    Issue or sign a statement of work, and set what a service promises.

    Money and commitment are the same authority. A contract is the company
    binding itself to a fixed scope at a fixed price.
    """

    message = "Issuing or signing a statement of work is a commercial decision."

    def has_permission(self, request, view) -> bool:
        return super().has_permission(request, view) and request.user.can_commit


class CanManageAccess(IsStaff):
    """
    Roles, staff invitations, deactivation, and who at a client can see what.

    FOUNDER ONLY, and the narrowest of the three on purpose: this is the
    permission that grants every other permission, including to itself. Anyone
    holding it can make themselves anything, so it is held by the person the
    charter already makes accountable.
    """

    message = "Only a founder can change who has access."

    def has_permission(self, request, view) -> bool:
        return super().has_permission(request, view) and request.user.can_manage_access


class CanConfigureBilling(IsStaff):
    """
    Edit the company's own billing identity.

    FOUNDER ONLY. Separate from CanCommit, which is about binding the company
    to a price: this is about which account the money arrives in. Changing the
    paybill or the bank details silently redirects every invoice issued after
    it, from a document that otherwise looks entirely correct, and the first
    sign of trouble is a client insisting they paid.
    """

    message = "Only a founder can change the company's billing details."

    def has_permission(self, request, view) -> bool:
        return super().has_permission(request, view) and request.user.can_configure_billing
