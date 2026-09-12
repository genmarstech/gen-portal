"""
The one endpoint in this project that answers without a session.

Everything here is written from the position of somebody with no account, no
cookie and no invitation, because that is who can call it.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from accounts.models import Organisation, User
from portal.models import Doc

pytestmark = pytest.mark.django_db


@pytest.fixture
def published() -> Doc:
    return Doc.objects.create(
        slug="business-platform",
        title="Genmars Business Platform",
        summary="Reports, inventory, permissions and integrations for SMEs.",
        category=Doc.Category.PLATFORM,
        body="## What it is\n\nA reusable core platform.",
        repo_url="https://github.com/genmarstech/business-os",
        is_published=True,
        status=Doc.Status.BUILDING,
        status_note="Core modules and reporting are working; permissions next.",
    )


@pytest.fixture
def draft() -> Doc:
    return Doc.objects.create(
        slug="unannounced-thing",
        title="Something we have not announced",
        summary="Internal only, for now.",
        body="Not for the public.",
        is_published=False,
    )


# ── who can read it ─────────────────────────────────────────────────────────


def test_anyone_can_read_published_documentation(client, published):
    response = client.get(reverse("public-docs"))
    assert response.status_code == 200
    slugs = [d["slug"] for d in response.json()["docs"]]
    assert slugs == ["business-platform"]


def test_a_draft_is_invisible_to_the_public_list(client, published, draft):
    response = client.get(reverse("public-docs"))
    body = response.content.decode()
    assert "unannounced-thing" not in body
    assert "Not for the public" not in body


def test_a_draft_is_404_rather_than_403(client, draft):
    """
    Same rule as the rest of the project. A 403 would confirm the slug exists
    and is being worked on, which is a free look at the roadmap.
    """
    response = client.get(reverse("public-doc", args=[draft.slug]))
    assert response.status_code == 404


def test_a_published_document_is_readable_by_slug(client, published):
    response = client.get(reverse("public-doc", args=[published.slug]))
    assert response.status_code == 200
    assert response.json()["title"] == "Genmars Business Platform"


# ── what it must never leak ─────────────────────────────────────────────────


def test_the_public_payload_names_nobody(client, published):
    """
    `updated_by` exists on the model and must not be serialised. Who on the
    team last edited a page is not the public's business, and a name plus a
    timestamp is more than it looks — it says who was working and when.
    """
    editor = User.objects.create_user(email="staff@genmars.co.ke", password="x")
    published.updated_by = editor
    published.save(update_fields=["updated_by"])

    body = client.get(reverse("public-docs")).content.decode()
    assert "staff@genmars.co.ke" not in body
    assert "updated_by" not in body


def test_the_public_endpoint_reaches_no_other_table(client, published):
    """
    A canary. If somebody adds a related field to the serializer, an
    organisation name is the first thing likely to ride along with it.
    """
    Organisation.objects.create(name="Kilimani Dental")

    body = client.get(reverse("public-docs")).content.decode()
    assert "Kilimani Dental" not in body


def test_no_parameter_widens_the_queryset(client, published, draft):
    """
    The filter is not optional. Anything that looks like it might turn drafts
    on is tried here, because the cost of one working is the roadmap.
    """
    for query in (
        "?is_published=false",
        "?all=1",
        "?status=planned",
        "?include_drafts=true",
    ):
        body = client.get(reverse("public-docs") + query).content.decode()
        assert "unannounced-thing" not in body, query


# ── the progress note, which is the field most likely to go stale ───────────


def test_a_progress_note_is_published_with_its_date(client, published):
    """
    The note and the date it was written are one fact. Serving the words
    without the date lets a page imply somebody checked this morning.
    """
    doc = client.get(reverse("public-doc", args=[published.slug])).json()
    assert doc["status_note"]
    assert doc["status_changed_at"], "a note was published with no date on it"


def test_status_labels_come_from_the_server(client, published):
    """
    So /docs and /services cannot end up calling the same state different
    things. "In development" is written once, in the model.
    """
    labels = {s["key"]: s["label"] for s in client.get(reverse("public-docs")).json()["statuses"]}
    assert labels["building"] == "In development"
