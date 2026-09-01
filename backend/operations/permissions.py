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
