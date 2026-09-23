"""
The work page, read by somebody with no account.

══════════════════════════════════════════════════════════════════════════════
THE CONSENT GATE IS THE WHOLE POINT OF THIS FILE.

Charter 04 §V: client-owned software carries the client's brand, and Genmars
is credited only with WRITTEN permission. Publishing a client's name without
it is the kind of thing that costs a relationship, and it would be published
by somebody in a hurry ticking one box — so the refusal lives in the queryset
that answers the internet, not in an editor's memory.

Everything here is written from the position of a stranger, because that is
who can call this endpoint.
══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from portal.models import WorkItem

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def an_empty_table(request):
    """
    Start from nothing, unless the test asks for the shipped rows.

    Migration 0041 seeds the three things Genmars had actually built, so the
    table is NOT empty in a fresh database. These tests are about what the
    queryset does with a given row — a rule that must not depend on which
    projects the company happened to have in September 2026 — so they clear it
    and build the case they mean.

    The one test that asserts the SEED is correctly gated marks itself
    `seeded` and keeps the rows. Both matter: the rule, and the data the rule
    is currently applied to.
    """
    if "seeded" not in request.keywords:
        WorkItem.objects.all().delete()


@pytest.fixture
def ours() -> WorkItem:
    """Something Genmars owns. Nobody to ask."""
    return WorkItem.objects.create(
        slug="our-own-product",
        name="Genmars Business Platform",
        category=WorkItem.Category.SOFTWARE,
        label=WorkItem.Label.PRODUCT,
        sector="Retail & multi-branch operations",
        year="2026",
        url="https://business.genmars.co.ke",
        summary="A multi-tenant point of sale and back office.",
        capabilities="Multi-tenant isolation\nPoint of sale\nVAT-inclusive pricing",
        is_published=True,
    )


@pytest.fixture
def theirs_unsigned() -> WorkItem:
    """A client's system, with no permission on file."""
    return WorkItem.objects.create(
        slug="a-client-site",
        name="A Client We Have Not Asked",
        category=WorkItem.Category.SITE,
        label=WorkItem.Label.CLIENT,
        summary="A booking site.",
        is_published=True,
        permission_on_file=False,
    )


def work(client) -> dict:
    response = client.get(reverse("public-work"))
    assert response.status_code == 200
    return response.json()


# ── the gate ────────────────────────────────────────────────────────────────


def test_our_own_product_needs_nobody_s_permission(client, ours):
    """
    The control, and the thing the website's old gate got wrong: it hid our
    own product behind two unrelated clients' signatures.
    """
    names = [i["name"] for i in work(client)["work"]]
    assert "Genmars Business Platform" in names


def test_a_client_without_written_permission_is_invisible(client, theirs_unsigned):
    """
    Published and still absent. The tick box is not the gate.
    """
    body = work(client)
    assert body["work"] == []


def test_the_same_client_appears_once_the_permission_is_on_file(
    client, theirs_unsigned
):
    """
    The positive control. Without it, the refusal above could just as well
    mean the endpoint is broken for everybody.
    """
    theirs_unsigned.permission_on_file = True
    theirs_unsigned.save()

    names = [i["name"] for i in work(client)["work"]]
    assert "A Client We Have Not Asked" in names


def test_one_missing_signature_no_longer_hides_everything_else(
    client, ours, theirs_unsigned
):
    """
    The old website rule was all-or-nothing across every entry. That was right
    about client work and wrong about ours, so the gate is now per item.
    """
    names = [i["name"] for i in work(client)["work"]]
    assert names == ["Genmars Business Platform"]


def test_an_unpublished_item_is_invisible_even_with_permission(client):
    WorkItem.objects.create(
        slug="not-yet",
        name="Signed But Not Ready",
        label=WorkItem.Label.CLIENT,
        summary="Waiting on a final read-through.",
        is_published=False,
        permission_on_file=True,
    )
    assert work(client)["work"] == []


# ── what the payload says ───────────────────────────────────────────────────


def test_capabilities_arrive_as_a_list_not_a_textarea(client, ours):
    item = work(client)["work"][0]
    assert item["capabilities"] == [
        "Multi-tenant isolation",
        "Point of sale",
        "VAT-inclusive pricing",
    ]


def test_the_payload_names_nobody_on_staff(client, ours):
    """
    Same rule as /docs: which member of the team wrote this is nobody else's
    business, and `updated_by` exists on the model precisely so it can be
    left out here.
    """
    body = work(client)
    assert "updated_by" not in body["work"][0]
    assert "updated_by" not in str(body)


def test_only_categories_with_something_in_them_are_listed(client, ours):
    """A heading with nothing under it is a gap the reader assumes is a bug."""
    keys = [c["key"] for c in work(client)["categories"]]
    assert keys == ["software"]


def test_it_answers_without_a_session(client, ours):
    """No cookie, no header, no account. That is the contract."""
    response = client.get(reverse("public-work"))
    assert response.status_code == 200
    assert response.wsgi_request.user.is_anonymous


# ── the rows we actually shipped ────────────────────────────────────────────


@pytest.mark.seeded
def test_neither_client_in_the_seeded_data_is_named_publicly(client):
    """
    Not a hypothetical. Migration 0041 carried two real client projects into
    this table, and neither client has been asked yet. Until they are, their
    names must not be on genmars.co.ke — and the reason this is a test rather
    than a careful migration is that the next person to tick a box will not
    have read the migration.
    """
    held = WorkItem.objects.filter(label=WorkItem.Label.CLIENT)
    assert held.exists(), "a seed with no client rows would not test the gate"
    assert not held.filter(permission_on_file=True).exists()

    payload = str(work(client))
    for item in held:
        assert item.name not in payload


@pytest.mark.seeded
def test_our_platform_is_the_one_thing_the_seed_publishes(client):
    names = [i["name"] for i in work(client)["work"]]
    assert names == ["Genmars Business Platform"]
