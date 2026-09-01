"""
One error shape for the whole API.

Every frontend in this company reads `data.detail` for the message and
`data.field` to put it next to the right input — gen-portal/frontend/src/lib/
api.ts does, and internals-tm does the same. DRF's default for a validation
error is neither: it is `{"scope": ["This field may not be blank."]}`, with the
field as the KEY, so `detail` is undefined and the frontend falls back to
"Something went wrong. Try again."

That fallback was hiding real, useful messages — the server knew exactly what
was wrong with the request and the user was told nothing. This normalises
validation errors into the shape the clients already expect.

Nothing here changes WHICH requests fail or what status they return. It changes
only how the failure is described, and only for responses that were previously
unreadable by the frontends.
"""

from __future__ import annotations

from rest_framework.exceptions import ValidationError
from rest_framework.views import exception_handler as drf_exception_handler


def _first_message(value) -> str | None:
    """The first human-readable string inside DRF's nested error structure."""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        for item in value:
            found = _first_message(item)
            if found:
                return found
        return None
    if isinstance(value, dict):
        for item in value.values():
            found = _first_message(item)
            if found:
                return found
    return None


def api_exception_handler(exc, context):
    response = drf_exception_handler(exc, context)
    if response is None or not isinstance(exc, ValidationError):
        return response

    detail = response.data

    # A non-field error: DRF puts it under this key, and there is no input to
    # attach it to.
    if isinstance(detail, dict) and "non_field_errors" in detail:
        response.data = {"detail": _first_message(detail["non_field_errors"])}
        return response

    if isinstance(detail, dict) and detail:
        field = next(iter(detail))
        message = _first_message(detail[field])
        if message:
            # `errors` keeps the original structure, so a form that wants to
            # mark up several fields at once still can. `detail` and `field`
            # are what the current frontends read.
            response.data = {"detail": message, "field": field, "errors": detail}
        return response

    if isinstance(detail, list):
        response.data = {"detail": _first_message(detail)}

    return response
