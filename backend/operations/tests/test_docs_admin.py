"""
Who may change what the company says in public, and what gets recorded.

Charter 02 §I puts public statements with the founder. These assert that the
permission actually holds at the HTTP layer rather than in a docstring.
"""

from __future__ import annotations

import pytest
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from portal.models import ActivityLog, Doc

pytestmark = pytest.mark.django_db


def staff(email: str, role: str = "") -> User:
    user = User.objects.create_user(email=email, password="x", full_name="A Person")
    user.is_staff = True
    user.staff_role = role
    user.email_verified_at = timezone.now()
    user.save()
    return user


@pytest.fixture
def founder() -> User:
    return staff("founder@genmars.co.ke", "founder")


@pytest.fixture
def engineer() -> User:
    return staff("engineer@genmars.co.ke", "delivery")


@pytest.fixture
def doc() -> Doc:
    return Doc.objects.create(
        slug="business-platform",
        title="Genmars Business Platform",
        summary="Reports, inventory, permissions and integrations.",
        body="## What it is",
    )


NEW = {
    "slug": "portal",
    "title": "The client portal",
    "summary": "Where clients see orders, invoices and progress.",
    "body": "## Signing in",
}


# ── who may write ───────────────────────────────────────────────────────────


def test_any_staff_may_read_the_documentation(client, engineer, doc):
    client.force_login(engineer)
    response = client.get(reverse("ops-docs"))
    assert response.status_code == 200
    assert response.json()["may_edit"] is False


def test_a_non_founder_cannot_create(client, engineer):
    client.force_login(engineer)
    response = client.post(
        reverse("ops-docs"), NEW, content_type="application/json"
    )
    assert response.status_code == 403
    assert not Doc.objects.filter(slug="portal").exists()


def test_a_non_founder_cannot_publish(client, engineer, doc):
    client.force_login(engineer)
    response = client.patch(
        reverse("ops-doc", args=[doc.pk]),
        {"is_published": True},
        content_type="application/json",
    )
    assert response.status_code == 403
    doc.refresh_from_db()
    assert doc.is_published is False


def test_a_founder_can_create_and_publish(client, founder):
    client.force_login(founder)
    created = client.post(reverse("ops-docs"), NEW, content_type="application/json")
    assert created.status_code == 201

    doc = Doc.objects.get(slug="portal")
    published = client.patch(
        reverse("ops-doc", args=[doc.pk]),
        {"is_published": True},
        content_type="application/json",
    )
    assert published.status_code == 200
    doc.refresh_from_db()
    assert doc.is_published and doc.published_at is not None


def test_a_signed_out_stranger_gets_nothing(client, doc):
    assert client.get(reverse("ops-docs")).status_code in (401, 403)


# ── the progress note and its date ──────────────────────────────────────────


def test_editing_the_body_does_not_re_date_the_progress_note(client, founder, doc):
    """
    The point of the date. If a typo fix re-dated the note, every note would
    look confirmed-this-morning and the field would be worth nothing.
    """
    client.force_login(founder)
    client.patch(
        reverse("ops-doc", args=[doc.pk]),
        {"status_note": "Reporting works; permissions next."},
        content_type="application/json",
    )
    doc.refresh_from_db()
    first_dated = doc.status_changed_at
    assert first_dated is not None

    client.patch(
        reverse("ops-doc", args=[doc.pk]),
        {"body": "## What it is\n\nA reusable core platform."},
        content_type="application/json",
    )
    doc.refresh_from_db()
    assert doc.status_changed_at == first_dated


def test_changing_the_note_re_dates_it(client, founder, doc):
    client.force_login(founder)
    client.patch(
        reverse("ops-doc", args=[doc.pk]),
        {"status_note": "Reporting works."},
        content_type="application/json",
    )
    doc.refresh_from_db()
    first = doc.status_changed_at

    client.patch(
        reverse("ops-doc", args=[doc.pk]),
        {"status_note": "Reporting and permissions work."},
        content_type="application/json",
    )
    doc.refresh_from_db()
    assert doc.status_changed_at > first


def test_a_stale_note_is_flagged_to_operations(client, founder, doc):
    """
    Shown to staff, never to the public — a visitor gets the date and their
    own judgement. Hiding an old note while the page still looked current
    would be the dishonest version.
    """
    doc.status_note = "Half built."
    doc.status_changed_at = timezone.now() - timezone.timedelta(days=90)
    doc.save()

    client.force_login(founder)
    row = next(
        d for d in client.get(reverse("ops-docs")).json()["docs"] if d["id"] == doc.pk
    )
    assert row["status_is_stale"] is True


# ── validation that protects the public page ────────────────────────────────


def test_a_repo_link_must_be_a_github_repository(client, founder, doc):
    client.force_login(founder)
    for bad in (
        "https://example.com/genmarstech/gen-portal",
        "http://github.com/genmarstech/gen-portal",
        "https://github.com/genmarstech",
    ):
        response = client.patch(
            reverse("ops-doc", args=[doc.pk]),
            {"repo_url": bad},
            content_type="application/json",
        )
        assert response.status_code == 400, bad
        assert response.json()["field"] == "repo_url"


def test_a_real_repository_link_is_accepted(client, founder, doc):
    client.force_login(founder)
    response = client.patch(
        reverse("ops-doc", args=[doc.pk]),
        {"repo_url": "https://github.com/genmarstech/gen-portal"},
        content_type="application/json",
    )
    assert response.status_code == 200


def test_two_documents_cannot_share_an_address(client, founder, doc):
    client.force_login(founder)
    response = client.post(
        reverse("ops-docs"),
        {**NEW, "slug": doc.slug},
        content_type="application/json",
    )
    assert response.status_code == 400
    assert response.json()["field"] == "slug"


# ── the log ─────────────────────────────────────────────────────────────────


def test_publishing_is_logged_and_ordinary_edits_are_not(client, founder, doc):
    client.force_login(founder)

    client.patch(
        reverse("ops-doc", args=[doc.pk]),
        {"summary": "A slightly better sentence."},
        content_type="application/json",
    )
    assert not ActivityLog.objects.filter(action__startswith="doc.").exists()

    client.patch(
        reverse("ops-doc", args=[doc.pk]),
        {"is_published": True},
        content_type="application/json",
    )
    entry = ActivityLog.objects.get(action=ActivityLog.Action.DOC_PUBLISHED)
    assert doc.title in entry.subject
    # It must say what publishing actually does, because a save is not live.
    assert "deploy" in entry.summary


def test_withdrawing_is_logged(client, founder, doc):
    doc.is_published = True
    doc.save()

    client.force_login(founder)
    client.patch(
        reverse("ops-doc", args=[doc.pk]),
        {"is_published": False},
        content_type="application/json",
    )
    assert ActivityLog.objects.filter(
        action=ActivityLog.Action.DOC_UNPUBLISHED
    ).exists()


def test_deleting_a_live_document_is_logged_as_a_withdrawal(client, founder, doc):
    doc.is_published = True
    doc.save()

    client.force_login(founder)
    response = client.delete(reverse("ops-doc", args=[doc.pk]))
    assert response.status_code == 204

    entry = ActivityLog.objects.get(action=ActivityLog.Action.DOC_UNPUBLISHED)
    assert "404" in entry.summary, "the log should say links to it will break"
