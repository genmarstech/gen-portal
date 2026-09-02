"""
Test-session setup.

The one thing in here is a workaround for a Django/Python incompatibility, not
a change to how this project behaves.
"""

from __future__ import annotations

import sys
from copy import copy


def _patch_django_context_copy_on_python_314() -> None:
    """
    Make `copy(Context)` work on Python 3.14.

    ── WHAT IS BROKEN ──────────────────────────────────────────────────────────

    Django 5.1's BaseContext.__copy__ is written as:

        def __copy__(self):
            duplicate = copy(super())
            duplicate.dicts = self.dicts[:]

    `copy(super())` relied on copy() unwrapping the super proxy to the instance
    behind it. Python 3.14 no longer does, so `duplicate` comes back as a bare
    `super` object and the next line raises AttributeError.

    ── WHY THIS SHOWS UP AS TEST FAILURES AND NOTHING ELSE ─────────────────────

    Nothing in this project copies a template context. Django's TEST CLIENT
    does: `django.test.client.store_rendered_templates` copies the context of
    every template rendered during a request so tests can assert on it.

    So this fires only when a template is rendered inside a test-client request,
    which is why it began affecting twenty-odd tests the day the auth emails
    started rendering HTML templates. Production is unaffected — the API image
    and CI both run Python 3.13, where `copy(super())` still works — but a local
    suite that cannot be run is a suite nobody will notice a real failure in.

    ── WHY PATCH RATHER THAN WORK AROUND IT IN OUR CODE ────────────────────────

    The alternative is not rendering templates during client-driven tests, which
    would mean either not sending HTML email or not testing the flows that send
    it. Both are worse than a six-line shim that is scoped to the interpreter
    that needs it and disappears on its own when we move to a Django that has
    fixed this upstream.
    """
    if sys.version_info < (3, 14):
        return

    from django.template.context import BaseContext

    def __copy__(self):  # noqa: N807 - it is a dunder on purpose
        duplicate = object.__new__(type(self))
        duplicate.__dict__.update(self.__dict__)
        duplicate.dicts = self.dicts[:]
        return duplicate

    BaseContext.__copy__ = __copy__


_patch_django_context_copy_on_python_314()

# Silence the unused-import warning: `copy` is referenced in the docstring
# above only. Kept out of the function body deliberately — the replacement
# does not need it.
del copy
