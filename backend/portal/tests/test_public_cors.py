"""
The one namespace in this Django that answers a cross-origin browser.

═══════════════════════════════════════════════════════════════════════════════
THE TEST THAT MATTERS IS test_the_scoped_and_staff_apis_never_send_the_header.

/api/public/ is readable from genmars.co.ke because the marketing site refreshes
/work in the visitor's browser. That is safe for exactly one reason: those views
read no credential at all, so there is nothing for a cross-origin caller to
borrow.

/api/ and /api/ops/ are the opposite — they are scoped to whoever is calling.
The day one of them starts sending Access-Control-Allow-Origin, any page on the
internet can read one client's orders, or every client's, using the visitor's
own session. That test enumerates the URLconf rather than naming routes, so a
route added later is covered the day it is added.

And Access-Control-Allow-Credentials must never appear anywhere. There is no
session to act as on /api/public/ today; the header must not be sitting there
waiting for the day somebody adds one.
═══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import pytest
from django.urls import get_resolver, reverse

pytestmark = pytest.mark.django_db

ALLOWED = "https://genmars.co.ke"
STRANGER = "https://not-genmars.example"

PUBLIC = "public-work"


# ── the opening ─────────────────────────────────────────────────────────────


def test_an_allowed_origin_may_read_it(client):
    response = client.get(reverse(PUBLIC), headers={"origin": ALLOWED})
    assert response.status_code == 200
    assert response["Access-Control-Allow-Origin"] == ALLOWED


def test_an_unknown_origin_gets_no_header(client):
    """
    The body still returns 200 — it is public, and curl has no origin. What an
    unknown origin does not get is the browser's permission to READ it.
    """
    response = client.get(reverse(PUBLIC), headers={"origin": STRANGER})
    assert response.status_code == 200
    assert not response.has_header("Access-Control-Allow-Origin")


def test_the_header_is_the_origin_not_a_wildcard(client):
    response = client.get(reverse(PUBLIC), headers={"origin": ALLOWED})
    assert response["Access-Control-Allow-Origin"] != "*"


def test_vary_origin_is_always_set(client):
    """
    Without it a shared cache serves the allowed origin's headers to everybody,
    or the reverse — and which way round it breaks depends on who asked first.
    """
    for origin in (ALLOWED, STRANGER):
        response = client.get(reverse(PUBLIC), headers={"origin": origin})
        assert "Origin" in response["Vary"]

    no_origin = client.get(reverse(PUBLIC))
    assert "Origin" in no_origin["Vary"]


# ── the limits ──────────────────────────────────────────────────────────────


def test_credentials_are_never_allowed(client):
    """
    The header that would turn a readable endpoint into one acting as somebody
    else. Checked on the preflight as well as the response, because a browser
    reads it from either.
    """
    for response in (
        client.get(reverse(PUBLIC), headers={"origin": ALLOWED}),
        client.options(reverse(PUBLIC), headers={"origin": ALLOWED}),
    ):
        assert not response.has_header("Access-Control-Allow-Credentials")


def test_the_preflight_advertises_reads_only(client):
    response = client.options(
        reverse(PUBLIC),
        headers={
            "origin": ALLOWED,
            "access-control-request-method": "GET",
        },
    )
    assert response.status_code == 200
    allowed = response["Access-Control-Allow-Methods"]
    assert allowed == "GET, HEAD, OPTIONS"
    for verb in ("POST", "PUT", "PATCH", "DELETE"):
        assert verb not in allowed


def test_writes_are_refused_whatever_the_origin_says(client):
    """The advertisement above is a claim; this is the enforcement."""
    for method in (client.post, client.put, client.patch, client.delete):
        response = method(reverse(PUBLIC), headers={"origin": ALLOWED})
        assert response.status_code == 405


def test_no_session_is_read_on_a_public_view():
    """
    The property the whole opening rests on. Asserted against the classes
    rather than a response, because an empty list is the thing that must stay
    empty — a response only shows that nobody happened to be logged in.
    """
    from portal import public_api

    for view in (
        public_api.WorkListView,
        public_api.DocListView,
        public_api.DocDetailView,
    ):
        assert view.authentication_classes == [], view.__name__
        assert issubclass(view, public_api.PublicRead), view.__name__


# ── the boundary ────────────────────────────────────────────────────────────


def _routes(prefix: str) -> list[str]:
    """Every registered route under a prefix, from the live URLconf."""
    found = []

    def walk(patterns, base=""):
        for entry in patterns:
            path = base + str(entry.pattern)
            if hasattr(entry, "url_patterns"):
                walk(entry.url_patterns, path)
            else:
                found.append("/" + path)

    walk(get_resolver().url_patterns)
    return [r for r in found if r.startswith(prefix)]


def test_the_scoped_and_staff_apis_never_send_the_header(client):
    """
    Enumerated from the URLconf, so a route added next month is covered the day
    it is added rather than the day somebody remembers this file.

    Any status is fine — 401, 403, 404, 405. What is not fine is the header,
    which would let any page on the internet read the response with the
    visitor's own session.
    """
    scoped = [
        r
        for r in _routes("/api/")
        if not r.startswith("/api/public/") and "<" not in r
    ]
    assert scoped, "no scoped routes found — this test would pass vacuously"

    leaked = []
    for route in scoped:
        response = client.get(route, headers={"origin": ALLOWED})
        if response.has_header("Access-Control-Allow-Origin"):
            leaked.append(route)

    assert not leaked, f"cross-origin readable: {leaked}"


def test_the_public_namespace_is_the_only_one_that_does(client):
    """
    Falsifiability for the test above: if PublicRead stopped applying the
    header, that test would pass while proving nothing. This one fails first.
    """
    public = [r for r in _routes("/api/public/") if "<" not in r]
    assert public, "no public routes found"

    allowed = [
        r
        for r in public
        if client.get(r, headers={"origin": ALLOWED}).has_header(
            "Access-Control-Allow-Origin"
        )
    ]
    assert allowed == public, f"public routes NOT readable: {set(public) - set(allowed)}"
