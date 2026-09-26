"""
The media shelf: what may be stored, and the one route that serves inline.

══════════════════════════════════════════════════════════════════════════════
THE PREVIEW ROUTE IS WHY THIS FILE EXISTS.

Everything else here is the library's rules again with a bigger ceiling. What
is genuinely new is `MediaPreviewView`, which serves a stored file with
Content-Disposition: inline — the exact thing settings.py and
portal/attachments.py say never to do.

Four conditions make it safe, and each one is a test below: the type comes
from the bytes, only raster and video are previewable, nosniff is set, and a
sandbox CSP is set. If any of them is ever removed the route has to go with
it, so each is asserted separately rather than in one lump.
══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import io
import struct
import zipfile

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from accounts.models import User
from portal.models import ActivityLog, MediaAsset

pytestmark = pytest.mark.django_db

PASSWORD = "correct-horse-battery"
LIST = "/api/ops/media"


# ── the files ────────────────────────────────────────────────────────────────


def a_png(width: int = 1920, height: int = 1080, name: str = "logo.png"):
    """A real PNG header. The IHDR is what `dimensions` reads."""
    ihdr = b"IHDR" + struct.pack(">II", width, height) + b"\x08\x06\x00\x00\x00"
    body = (
        b"\x89PNG\r\n\x1a\n"
        + struct.pack(">I", 13)
        + ihdr
        + b"\x00\x00\x00\x00"  # crc, unchecked
        + b"0" * 64
    )
    return SimpleUploadedFile(name, body, content_type="image/png")


def an_mp4(brand: bytes = b"isom", name: str = "promo.mp4"):
    body = b"\x00\x00\x00\x20" + b"ftyp" + brand + b"\x00" * 4 + b"0" * 128
    # The browser's claim, which nothing here trusts.
    return SimpleUploadedFile(name, body, content_type="video/mp4")


def a_pdf(name: str = "brandbook.pdf"):
    return SimpleUploadedFile(name, b"%PDF-1.7\n" + b"0" * 128, content_type="application/pdf")


def an_svg(name: str = "logo.svg"):
    """The format the whole inline-serving argument turns on."""
    body = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    return SimpleUploadedFile(name, body, content_type="image/svg+xml")


# ── the people ───────────────────────────────────────────────────────────────


@pytest.fixture
def founder() -> User:
    return User.objects.create_user(
        email="edwin@genmars.co.ke", password=PASSWORD, full_name="Edwin",
        is_staff=True, staff_role=User.StaffRole.FOUNDER,
    )


@pytest.fixture
def engineer() -> User:
    return User.objects.create_user(
        email="dev@genmars.co.ke", password=PASSWORD, full_name="An Engineer",
        is_staff=True, staff_role=User.StaffRole.DELIVERY,
    )


@pytest.fixture
def outsider() -> User:
    return User.objects.create_user(email="someone@acme.example", password=PASSWORD)


def upload(client, **fields):
    body = {"title": "An asset", "shelf": MediaAsset.Shelf.BRAND}
    body.update(fields)
    body.setdefault("file", a_png())
    return client.post(LIST, body)


# ── 1. what may be stored ────────────────────────────────────────────────────


def test_a_video_is_kept(client, founder):
    """The format the library refuses and this shelf exists for."""
    client.force_login(founder)
    response = upload(client, file=an_mp4(), title="Promo film")
    assert response.status_code == 201
    assert response.json()["content_type"] == "video/mp4"
    assert response.json()["is_video"] is True


@pytest.mark.parametrize("brand", [b"isom", b"mp42", b"avc1", b"qt  "])
def test_the_iso_brands_we_take(client, founder, brand):
    client.force_login(founder)
    assert upload(client, file=an_mp4(brand)).status_code == 201


def test_an_unknown_iso_brand_is_refused(client, founder):
    """`ftyp` alone identifies nothing — the brand has to be one we play."""
    client.force_login(founder)
    response = upload(client, file=an_mp4(b"XXXX"))
    assert response.status_code == 400
    assert MediaAsset.objects.count() == 0


def test_an_svg_is_refused_however_it_is_labelled(client, founder):
    """
    The single most important refusal on this model. An SVG is a script
    container, and this shelf serves files inline.
    """
    client.force_login(founder)
    response = upload(client, file=an_svg())
    assert response.status_code == 400
    assert MediaAsset.objects.count() == 0


def test_the_contact_log_did_not_gain_video(founder):
    """Adding a media shelf must not widen what a CLIENT may upload."""
    from portal import attachments

    with pytest.raises(attachments.AttachmentError):
        attachments.inspect(an_mp4())


def test_the_library_did_not_gain_video_either(founder):
    from portal import attachments

    with pytest.raises(attachments.AttachmentError):
        attachments.inspect(an_mp4(), documents=True)


def test_the_stored_path_is_not_the_uploaded_name(client, founder):
    client.force_login(founder)
    upload(client, file=a_png(name="../../etc/passwd.png"))
    asset = MediaAsset.objects.get()
    assert ".." not in asset.file.name
    assert "passwd" not in asset.file.name
    assert asset.file.name.startswith("media/brand/")
    assert asset.original_name == "passwd.png"


# ── 2. dimensions, read from the header ──────────────────────────────────────


def test_a_png_is_measured_without_decoding_it(client, founder):
    client.force_login(founder)
    body = upload(client, file=a_png(1920, 1080)).json()
    assert (body["width"], body["height"]) == (1920, 1080)
    assert body["aspect"] == pytest.approx(1.7778, abs=1e-3)


def test_something_unmeasurable_still_stores(client, founder):
    """Dimensions are cosmetic; failing to read them must not refuse a file."""
    client.force_login(founder)
    body = upload(client, file=an_mp4()).json()
    assert body["width"] is None and body["height"] is None
    assert body["aspect"] is None


# ── 3. the preview route ─────────────────────────────────────────────────────


def test_a_picture_previews_inline(client, founder):
    client.force_login(founder)
    pk = upload(client).json()["id"]
    response = client.get(f"{LIST}/{pk}/preview")
    assert response.status_code == 200
    assert "attachment" not in response.get("Content-Disposition", "")


def test_the_preview_sets_nosniff(client, founder):
    """Condition 3. Without it the browser may second-guess our type."""
    client.force_login(founder)
    pk = upload(client).json()["id"]
    assert client.get(f"{LIST}/{pk}/preview")["X-Content-Type-Options"] == "nosniff"


def test_the_preview_sets_a_sandbox_csp(client, founder):
    """Condition 4. Even a file past everything else executes nothing."""
    client.force_login(founder)
    pk = upload(client).json()["id"]
    csp = client.get(f"{LIST}/{pk}/preview")["Content-Security-Policy"]
    assert "default-src 'none'" in csp
    assert "sandbox" in csp


def test_the_preview_is_never_publicly_cacheable(client, founder):
    client.force_login(founder)
    pk = upload(client).json()["id"]
    assert "private" in client.get(f"{LIST}/{pk}/preview")["Cache-Control"]


def test_a_pdf_is_storable_but_not_previewable(client, founder):
    """
    Condition 2, and the case that proves it is a real gate: a PDF passes
    the upload allowlist and is still refused an inline route, because a PDF
    is a scripting host.
    """
    client.force_login(founder)
    body = upload(client, file=a_pdf(), title="Brand book").json()
    assert body["preview_url"] is None
    assert client.get(f"{LIST}/{body['id']}/preview").status_code == 404


def test_a_video_is_previewable(client, founder):
    client.force_login(founder)
    body = upload(client, file=an_mp4()).json()
    assert body["preview_url"] == f"/api/ops/media/{body['id']}/preview"


def test_the_download_is_always_an_attachment(client, founder):
    client.force_login(founder)
    pk = upload(client).json()["id"]
    response = client.get(f"{LIST}/{pk}/file")
    assert response["Content-Disposition"].startswith("attachment")
    assert response["X-Content-Type-Options"] == "nosniff"


def test_the_serialised_row_carries_no_media_path(client, founder):
    client.force_login(founder)
    body = upload(client).json()
    assert body["url"] == f"/api/ops/media/{body['id']}/file"
    assert "file" not in body


# ── 4. who may do what ───────────────────────────────────────────────────────


def test_a_client_account_reaches_nothing(client, outsider):
    client.force_login(outsider)
    assert client.get(LIST).status_code == 403


def test_every_staff_account_sees_everything(client, founder, engineer):
    """One visibility tier here, unlike the library. See the model banner."""
    client.force_login(founder)
    upload(client, title="The logo")
    client.logout()

    client.force_login(engineer)
    body = client.get(LIST).json()
    assert len(body["assets"]) == 1
    assert body["can_delete"] is False


def test_only_a_founder_may_delete(client, founder, engineer):
    client.force_login(founder)
    pk = upload(client).json()["id"]
    client.logout()

    client.force_login(engineer)
    response = client.delete(f"{LIST}/{pk}")
    assert response.status_code == 400
    assert "Archive it instead" in response.json()["detail"]
    assert MediaAsset.objects.filter(pk=pk).exists()


def test_deleting_removes_the_bytes(client, founder):
    client.force_login(founder)
    pk = upload(client).json()["id"]
    asset = MediaAsset.objects.get(pk=pk)
    storage, path = asset.file.storage, asset.file.name

    assert client.delete(f"{LIST}/{pk}").status_code == 204
    assert not storage.exists(path)


def test_archiving_keeps_the_file(client, founder):
    client.force_login(founder)
    pk = upload(client).json()["id"]
    client.patch(f"{LIST}/{pk}", {"action": "archive"}, content_type="application/json")

    asset = MediaAsset.objects.get(pk=pk)
    assert asset.is_archived
    assert asset.file.storage.exists(asset.file.name)
    assert client.get(LIST).json()["assets"] == []
    assert len(client.get(f"{LIST}?archived=1").json()["assets"]) == 1


# ── 5. downloads are counted, never attributed ───────────────────────────────


def test_a_download_is_counted(client, founder):
    client.force_login(founder)
    pk = upload(client).json()["id"]

    client.get(f"{LIST}/{pk}/file")
    client.get(f"{LIST}/{pk}/file")
    assert MediaAsset.objects.get(pk=pk).download_count == 2


def test_a_preview_is_not_counted_as_a_download(client, founder):
    """A grid loads every tile. Counting that would make the number mean
    nothing."""
    client.force_login(founder)
    pk = upload(client).json()["id"]
    client.get(f"{LIST}/{pk}/preview")
    assert MediaAsset.objects.get(pk=pk).download_count == 0


def test_no_log_entry_names_who_downloaded(client, founder, engineer):
    """
    The deliberate absence. Which assets earn their place is worth knowing;
    which colleague fetched the logo is a surveillance record.
    """
    client.force_login(founder)
    pk = upload(client, title="The logo").json()["id"]
    client.logout()

    client.force_login(engineer)
    client.get(f"{LIST}/{pk}/file")

    for entry in ActivityLog.objects.all():
        assert "download" not in entry.action
        assert engineer.email not in str(entry.detail)
        assert engineer.email not in entry.summary


def test_upload_and_delete_are_logged(client, founder):
    client.force_login(founder)
    pk = upload(client, title="The logo").json()["id"]
    client.delete(f"{LIST}/{pk}")
    actions = set(ActivityLog.objects.values_list("action", flat=True))
    assert ActivityLog.Action.MEDIA_ADDED in actions
    assert ActivityLog.Action.MEDIA_REMOVED in actions


# ── 6. the list ──────────────────────────────────────────────────────────────


def test_an_asset_needs_a_name(client, founder):
    client.force_login(founder)
    response = upload(client, title="   ")
    assert response.status_code == 400
    assert response.json()["field"] == "title"


def test_empty_shelves_are_still_listed(client, founder):
    client.force_login(founder)
    shelves = client.get(LIST).json()["shelves"]
    assert len(shelves) == len(MediaAsset.Shelf.choices)


def test_the_totals_say_what_the_shelf_costs(client, founder):
    client.force_login(founder)
    upload(client, title="One")
    upload(client, title="Two", file=an_mp4())
    totals = client.get(LIST).json()["totals"]
    assert totals["count"] == 2
    assert totals["bytes"] > 0
