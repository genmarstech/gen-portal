"""
The company library: what may be stored, and who may see it.

══════════════════════════════════════════════════════════════════════════════
TWO THINGS ARE BEING PROVED HERE AND THEY ARE INDEPENDENT.

  1. WE DECIDE WHAT A FILE IS BY READING IT. This cabinet takes office
     documents, which are zip archives — so `.docx` is the first format in this
     system whose magic number identifies nothing at all. A test that only
     uploads a PDF would pass for the same reason the old code passed.

  2. A FOUNDERS-ONLY DOCUMENT IS ABSENT, NOT FORBIDDEN. 404 and not 403, in
     the list and on a direct fetch and on the download route, because "there
     is a document about me I may not read" is the fact the narrow visibility
     exists to keep quiet.

The uploads below are built byte by byte rather than read from fixtures. A
fixture file is a thing somebody has to trust; a zip assembled in the test is
a thing the test can lie about on purpose, which is what most of these do.
══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import io
import zipfile
from datetime import timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from portal.models import ActivityLog, LibraryFile

pytestmark = pytest.mark.django_db

PASSWORD = "correct-horse-battery"


# ── the files ────────────────────────────────────────────────────────────────


def a_pdf(name: str = "policy.pdf") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, b"%PDF-1.7\n" + b"0" * 64, content_type="application/pdf")


def an_ooxml(
    *, parts: list[str], name: str = "terms.docx", content_types: bool = True
) -> SimpleUploadedFile:
    """
    A zip shaped like an office document. `parts` is what goes inside, so a
    test can build a real .docx, a .docx missing its manifest, or a plain zip
    pretending to be one.
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        if content_types:
            archive.writestr("[Content_Types].xml", "<Types/>")
        for part in parts:
            archive.writestr(part, "x")
    buffer.seek(0)
    # The content_type is what a BROWSER would claim. Every test here that
    # passes does so despite it, not because of it.
    return SimpleUploadedFile(
        name,
        buffer.read(),
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


def an_odf(mimetype: bytes, name: str = "notes.odt") -> SimpleUploadedFile:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("mimetype", mimetype)
        archive.writestr("content.xml", "<x/>")
    buffer.seek(0)
    return SimpleUploadedFile(name, buffer.read(), content_type="application/zip")


# ── the people ───────────────────────────────────────────────────────────────


@pytest.fixture
def founder() -> User:
    return User.objects.create_user(
        email="edwin@genmars.co.ke",
        password=PASSWORD,
        full_name="Edwin Muchemi",
        is_staff=True,
        staff_role=User.StaffRole.FOUNDER,
    )


@pytest.fixture
def engineer() -> User:
    return User.objects.create_user(
        email="dev@genmars.co.ke",
        password=PASSWORD,
        full_name="A Delivery Engineer",
        is_staff=True,
        staff_role=User.StaffRole.DELIVERY,
    )


@pytest.fixture
def outsider() -> User:
    return User.objects.create_user(email="someone@acme.example", password=PASSWORD)


def signed_in(client, user) -> None:
    client.force_login(user)


LIST = "/api/ops/library"


def upload(client, **fields):
    body = {"title": "A document", "shelf": LibraryFile.Shelf.POLICY}
    body.update(fields)
    if "file" not in body:
        body["file"] = a_pdf()
    return client.post(LIST, body)


# ── 1. what may be stored ────────────────────────────────────────────────────


def test_a_pdf_is_kept(client, founder):
    signed_in(client, founder)
    response = upload(client, title="Terms of Business")
    assert response.status_code == 201
    assert response.json()["content_type"] == "application/pdf"


def test_a_real_word_document_is_kept(client, founder):
    """The format the contact log refuses and this cabinet does not."""
    signed_in(client, founder)
    response = upload(client, file=an_ooxml(parts=["word/document.xml"]))
    assert response.status_code == 201
    assert response.json()["content_type"].endswith("wordprocessingml.document")
    assert response.json()["original_name"] == "terms.docx"


def test_a_spreadsheet_is_kept(client, founder):
    signed_in(client, founder)
    response = upload(client, file=an_ooxml(parts=["xl/workbook.xml"], name="ledger.xlsx"))
    assert response.status_code == 201
    assert response.json()["content_type"].endswith("spreadsheetml.sheet")


def test_opendocument_is_kept(client, founder):
    signed_in(client, founder)
    response = upload(
        client, file=an_odf(b"application/vnd.oasis.opendocument.text")
    )
    assert response.status_code == 201
    assert response.json()["content_type"] == "application/vnd.oasis.opendocument.text"


def test_a_plain_zip_named_docx_is_refused(client, founder):
    """
    The whole reason `_office` opens the container.

    This file has the .docx name, the .docx content-type header and the same
    first four bytes as a real one. Only the inside gives it away.
    """
    signed_in(client, founder)
    response = upload(
        client, file=an_ooxml(parts=["evil.exe"], content_types=False)
    )
    assert response.status_code == 400
    assert "zip archive" in response.json()["detail"]
    assert LibraryFile.objects.count() == 0


def test_a_macro_document_is_refused_and_says_why(client, founder):
    """
    A .docm renamed to .docx is still a .docm. The refusal names macros
    specifically, because "not a type we take" would send somebody hunting a
    format problem they do not have.
    """
    signed_in(client, founder)
    response = upload(
        client,
        file=an_ooxml(parts=["word/document.xml", "word/vbaProject.bin"]),
    )
    assert response.status_code == 400
    assert "macros" in response.json()["detail"]
    assert LibraryFile.objects.count() == 0


def test_an_executable_is_refused(client, founder):
    signed_in(client, founder)
    response = upload(
        client,
        file=SimpleUploadedFile("setup.pdf", b"MZ\x90\x00" + b"0" * 64, content_type="application/pdf"),
    )
    assert response.status_code == 400
    assert LibraryFile.objects.count() == 0


def test_the_contact_log_did_not_gain_office_documents(founder):
    """
    The library widened what THIS surface takes. It must not have widened what
    a client may upload to a conversation — `documents` defaults to False, and
    this is the test that says so out loud.
    """
    from portal import attachments

    with pytest.raises(attachments.AttachmentError):
        attachments.inspect(an_ooxml(parts=["word/document.xml"]))


def test_the_stored_path_is_not_the_uploaded_name(client, founder):
    """`attachment_path`'s rule, applied here: the filename is display text."""
    signed_in(client, founder)
    upload(client, file=a_pdf("../../etc/passwd.pdf"))
    document = LibraryFile.objects.get()
    assert "passwd" not in document.file.name
    assert ".." not in document.file.name
    assert document.file.name.startswith("library/policy/")
    # And it is still shown to a person as what they called it.
    assert document.original_name == "passwd.pdf"


# ── 2. who may see it ────────────────────────────────────────────────────────


def test_a_client_account_cannot_reach_the_library(client, outsider):
    signed_in(client, outsider)
    assert client.get(LIST).status_code == 403


def test_a_founder_only_document_is_absent_for_other_staff(
    client, founder, engineer
):
    signed_in(client, founder)
    created = upload(
        client, title="Bank mandate", visibility=LibraryFile.Visibility.FOUNDER
    )
    assert created.status_code == 201
    pk = created.json()["id"]

    client.logout()
    signed_in(client, engineer)

    listed = client.get(LIST).json()
    assert listed["documents"] == []
    # And the shelf count does not leak it either — a "1" beside a shelf with
    # nothing in it is the same disclosure in a smaller font.
    assert all(shelf["count"] == 0 for shelf in listed["shelves"])


@pytest.mark.parametrize("route", ["/api/ops/library/{pk}", "/api/ops/library/{pk}/file"])
def test_a_founder_only_document_is_404_not_403(client, founder, engineer, route):
    signed_in(client, founder)
    pk = upload(
        client, title="Advocate's letter", visibility=LibraryFile.Visibility.FOUNDER
    ).json()["id"]

    client.logout()
    signed_in(client, engineer)
    # 403 would confirm the row exists. Same reasoning as portal/selectors.py.
    assert client.get(route.format(pk=pk)).status_code == 404


def test_only_a_founder_may_restrict_a_document(client, engineer):
    signed_in(client, engineer)
    response = upload(client, visibility=LibraryFile.Visibility.FOUNDER)
    assert response.status_code == 400
    assert response.json()["field"] == "visibility"


def test_an_engineer_cannot_unrestrict_one_either(client, founder, engineer):
    """
    Lowering the bar is the dangerous direction, and the one a check written
    carelessly would miss.
    """
    signed_in(client, founder)
    pk = upload(client, visibility=LibraryFile.Visibility.FOUNDER).json()["id"]
    client.logout()

    signed_in(client, engineer)
    # They cannot even see it, so the refusal is a 404 rather than the
    # permission message — which is the stronger of the two answers.
    assert client.patch(
        f"/api/ops/library/{pk}",
        {"visibility": LibraryFile.Visibility.STAFF},
        content_type="application/json",
    ).status_code == 404


def test_ordinary_documents_are_shared(client, founder, engineer):
    """accounts/models.py: hiding the work from each other would be theatre."""
    signed_in(client, founder)
    upload(client, title="Brand guide")
    client.logout()

    signed_in(client, engineer)
    assert len(client.get(LIST).json()["documents"]) == 1


# ── 3. the bytes only leave one way ──────────────────────────────────────────


def test_a_download_is_never_rendered_in_our_origin(client, founder):
    signed_in(client, founder)
    pk = upload(client).json()["id"]

    response = client.get(f"/api/ops/library/{pk}/file")
    assert response.status_code == 200
    # The three headers that stop a stored document becoming stored XSS
    # against the member of staff who opens it.
    assert response["Content-Disposition"].startswith("attachment")
    assert response["X-Content-Type-Options"] == "nosniff"
    assert "sandbox" in response["Content-Security-Policy"]


def test_the_serialised_row_carries_no_media_path(client, founder):
    """MEDIA_URL is empty on purpose, and a serialiser is where that gets
    quietly undone."""
    signed_in(client, founder)
    body = upload(client).json()
    assert body["url"] == f"/api/ops/library/{body['id']}/file"
    assert "file" not in body


def test_a_missing_file_is_404_rather_than_500(client, founder):
    """A database restored without MEDIA_ROOT. Says so instead of crashing."""
    signed_in(client, founder)
    pk = upload(client).json()["id"]
    LibraryFile.objects.get(pk=pk).file.delete(save=False)
    assert client.get(f"/api/ops/library/{pk}/file").status_code == 404


# ── 4. archiving, superseding, expiry ────────────────────────────────────────


def test_archiving_keeps_the_file(client, founder):
    signed_in(client, founder)
    pk = upload(client).json()["id"]

    client.patch(
        f"/api/ops/library/{pk}", {"action": "archive"}, content_type="application/json"
    )
    document = LibraryFile.objects.get(pk=pk)
    assert document.is_archived
    assert document.file.storage.exists(document.file.name)
    # Off the shelf by default, and findable when asked for.
    assert client.get(LIST).json()["documents"] == []
    assert len(client.get(f"{LIST}?archived=1").json()["documents"]) == 1


def test_a_renewal_archives_what_it_replaces(client, founder):
    """
    The failure this library exists to prevent: two insurance certificates and
    no way to tell which is current.
    """
    signed_in(client, founder)
    old = upload(client, title="Insurance 2025").json()["id"]

    new = upload(client, title="Insurance 2026", replaces=old)
    assert new.status_code == 201

    superseded = LibraryFile.objects.get(pk=old)
    assert superseded.is_archived
    assert superseded.replaced_by_id == new.json()["id"]
    # One current certificate on the shelf, not two.
    assert [d["title"] for d in client.get(LIST).json()["documents"]] == ["Insurance 2026"]


def test_restoring_clears_the_supersession(client, founder):
    """A restored document is current, so it cannot still claim to have been
    replaced."""
    signed_in(client, founder)
    old = upload(client, title="Insurance 2025").json()["id"]
    upload(client, title="Insurance 2026", replaces=old)

    client.patch(
        f"/api/ops/library/{old}",
        {"action": "restore"},
        content_type="application/json",
    )
    document = LibraryFile.objects.get(pk=old)
    assert not document.is_archived
    assert document.replaced_by_id is None


def test_expiry_is_computed_not_stored(client, founder):
    signed_in(client, founder)
    yesterday = timezone.localdate() - timedelta(days=1)
    pk = upload(client, title="Lapsed cover", expires_on=yesterday.isoformat()).json()["id"]

    body = client.get(LIST).json()
    row = next(d for d in body["documents"] if d["id"] == pk)
    assert row["is_expired"] is True
    # And it is surfaced rather than left to be noticed.
    assert [d["id"] for d in body["attention"]] == [pk]


def test_something_lapsing_soon_is_flagged_before_it_does(client, founder):
    signed_in(client, founder)
    soon = timezone.localdate() + timedelta(days=10)
    pk = upload(client, title="Cover note", expires_on=soon.isoformat()).json()["id"]

    body = client.get(LIST).json()
    row = next(d for d in body["documents"] if d["id"] == pk)
    assert row["is_expired"] is False
    assert row["expires_soon"] is True
    assert [d["id"] for d in body["attention"]] == [pk]


def test_a_date_that_is_not_a_date_is_refused(client, founder):
    signed_in(client, founder)
    response = upload(client, expires_on="whenever")
    assert response.status_code == 400
    assert response.json()["field"] == "expires_on"


def test_an_expiry_can_be_cleared(client, founder):
    """`None` is a value here, which is why the service uses a sentinel."""
    signed_in(client, founder)
    pk = upload(client, expires_on="2027-01-01").json()["id"]

    client.patch(
        f"/api/ops/library/{pk}", {"expires_on": None}, content_type="application/json"
    )
    assert LibraryFile.objects.get(pk=pk).expires_on is None


# ── 5. deleting, and the record of it ────────────────────────────────────────


def test_only_a_founder_may_delete(client, founder, engineer):
    signed_in(client, founder)
    pk = upload(client).json()["id"]
    client.logout()

    signed_in(client, engineer)
    response = client.delete(f"/api/ops/library/{pk}")
    assert response.status_code == 400
    assert "Archive it instead" in response.json()["detail"]
    assert LibraryFile.objects.filter(pk=pk).exists()


def test_deleting_really_removes_the_bytes(client, founder):
    signed_in(client, founder)
    pk = upload(client).json()["id"]
    document = LibraryFile.objects.get(pk=pk)
    storage, path = document.file.storage, document.file.name

    assert client.delete(f"/api/ops/library/{pk}").status_code == 204
    assert not LibraryFile.objects.filter(pk=pk).exists()
    # A soft delete that kept the file while saying it was gone would be us
    # being untrue about our own system.
    assert not storage.exists(path)


def test_every_act_is_logged_and_the_log_keeps_no_secrets(client, founder):
    signed_in(client, founder)
    pk = upload(client, title="Bank mandate", description="Sort code 00-00-00").json()["id"]
    client.patch(
        f"/api/ops/library/{pk}", {"action": "archive"}, content_type="application/json"
    )
    client.delete(f"/api/ops/library/{pk}")

    actions = list(
        ActivityLog.objects.order_by("id").values_list("action", flat=True)
    )
    assert ActivityLog.Action.LIBRARY_ADDED in actions
    assert ActivityLog.Action.LIBRARY_ARCHIVED in actions
    assert ActivityLog.Action.LIBRARY_REMOVED in actions

    # The description can say why an advocate was engaged. The log is read by
    # everybody who can read the log.
    for entry in ActivityLog.objects.all():
        assert "Sort code" not in str(entry.detail)
        assert "Sort code" not in entry.summary


def test_an_edit_logs_which_fields_moved_not_what_they_became(client, founder):
    signed_in(client, founder)
    pk = upload(client, title="Old name").json()["id"]
    client.patch(
        f"/api/ops/library/{pk}",
        {"title": "New name"},
        content_type="application/json",
    )
    entry = ActivityLog.objects.filter(action=ActivityLog.Action.LIBRARY_UPDATED).get()
    assert "title" in entry.summary
    assert "New name" not in entry.summary


def test_a_document_needs_a_name(client, founder):
    signed_in(client, founder)
    response = upload(client, title="   ")
    assert response.status_code == 400
    assert response.json()["field"] == "title"


def test_empty_shelves_are_still_listed(client, founder):
    """An empty shelf is how somebody discovers nobody has ever filed a
    supplier agreement."""
    signed_in(client, founder)
    shelves = client.get(LIST).json()["shelves"]
    assert len(shelves) == len(LibraryFile.Shelf.choices)
    assert all(s["count"] == 0 for s in shelves)
