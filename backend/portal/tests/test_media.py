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


def test_a_video_is_not_previewable_until_its_lighter_cut_exists(client, founder):
    """
    This asserted the opposite until the preview cut existed, and the change
    is the point: a master is the wrong file to stream into a grid, so a
    video earns a preview_url only once `build_media_previews` has produced
    something small enough to play. Section 8 covers the rest.
    """
    client.force_login(founder)
    body = upload(client, file=an_mp4()).json()
    assert body["preview_url"] is None
    assert body["preview_state"] == "pending"


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


# ── 7. byte ranges, because a browser cannot play video without them ─────────
#
# Django's FileResponse implements no Range support at all: no `Range`, no
# `Accept-Ranges`, no 206 anywhere in it. Serving video through it means a
# <video> cannot seek, iOS Safari will not start playback, and a 200 MB
# download that drops at 90% starts again from zero.


def a_big_png(kb: int = 40) -> SimpleUploadedFile:
    """Large enough that a range is a real slice rather than the whole file."""
    ihdr = b"IHDR" + struct.pack(">II", 64, 64) + b"\x08\x06\x00\x00\x00"
    body = (
        b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + ihdr + b"\x00\x00\x00\x00"
    )
    body += bytes(range(256)) * (kb * 1024 // 256)
    return SimpleUploadedFile("big.png", body, content_type="image/png")


@pytest.fixture
def big(client, founder):
    client.force_login(founder)
    return upload(client, file=a_big_png(), title="Big").json()


def test_a_full_response_advertises_ranges(client, founder, big):
    """Without Accept-Ranges a client never learns it may ask, so never asks."""
    for route in ("preview", "file"):
        response = client.get(f"{LIST}/{big['id']}/{route}")
        assert response.status_code == 200
        assert response["Accept-Ranges"] == "bytes"


def test_a_range_request_gets_206_and_only_those_bytes(client, founder, big):
    response = client.get(f"{LIST}/{big['id']}/preview", HTTP_RANGE="bytes=100-199")
    assert response.status_code == 206
    assert response["Content-Range"] == f"bytes 100-199/{big['size_bytes']}"
    assert response["Content-Length"] == "100"
    assert len(b"".join(response.streaming_content)) == 100


def test_the_sliced_bytes_are_the_right_ones(client, founder, big):
    """A 206 with the wrong offset is a corrupt stream the browser blames on
    the codec."""
    whole = b"".join(client.get(f"{LIST}/{big['id']}/file").streaming_content)
    part = b"".join(
        client.get(f"{LIST}/{big['id']}/file", HTTP_RANGE="bytes=500-999").streaming_content
    )
    assert part == whole[500:1000]


def test_an_open_ended_range_runs_to_the_end(client, founder, big):
    """`bytes=N-` is what a browser sends to resume a download."""
    size = big["size_bytes"]
    response = client.get(f"{LIST}/{big['id']}/file", HTTP_RANGE=f"bytes={size - 10}-")
    assert response.status_code == 206
    assert response["Content-Range"] == f"bytes {size - 10}-{size - 1}/{size}"
    assert len(b"".join(response.streaming_content)) == 10


def test_a_suffix_range_is_the_LAST_n_bytes(client, founder, big):
    """`bytes=-500` means the last 500, not 'from 500'. Reading it the other
    way serves the wrong part of the file with a confident 206."""
    size = big["size_bytes"]
    whole = b"".join(client.get(f"{LIST}/{big['id']}/file").streaming_content)
    response = client.get(f"{LIST}/{big['id']}/file", HTTP_RANGE="bytes=-500")
    assert response.status_code == 206
    assert response["Content-Range"] == f"bytes {size - 500}-{size - 1}/{size}"
    assert b"".join(response.streaming_content) == whole[-500:]


@pytest.mark.parametrize(
    "header",
    ["bytes=999999999-", "bytes=abc", "items=0-10", "bytes=50-10", "", "bytes=-0"],
)
def test_a_range_we_cannot_satisfy_serves_the_whole_file(client, founder, big, header):
    """
    RFC 9110: ignore a Range you cannot make sense of and send the whole
    representation. A 400 here would break clients that send a header we
    simply did not anticipate — and a range past the end must never become a
    read outside the file.
    """
    response = client.get(f"{LIST}/{big['id']}/file", HTTP_RANGE=header)
    assert response.status_code == 200


def test_a_resumed_download_is_not_counted_again(client, founder, big):
    """
    Otherwise a 200 MB download resumed over a flaky link counts as twenty,
    and download_count stops meaning anything.
    """
    client.get(f"{LIST}/{big['id']}/file")
    assert MediaAsset.objects.get(pk=big["id"]).download_count == 1
    client.get(f"{LIST}/{big['id']}/file", HTTP_RANGE="bytes=0-99")
    client.get(f"{LIST}/{big['id']}/file", HTTP_RANGE="bytes=100-199")
    assert MediaAsset.objects.get(pk=big["id"]).download_count == 1


def test_a_partial_response_keeps_every_security_header(client, founder, big):
    """The 206 path builds its own response, so it is a second place the
    headers could go missing."""
    response = client.get(f"{LIST}/{big['id']}/preview", HTTP_RANGE="bytes=0-99")
    assert response["X-Content-Type-Options"] == "nosniff"
    assert "default-src 'none'" in response["Content-Security-Policy"]
    assert "sandbox" in response["Content-Security-Policy"]
    assert "private" in response["Cache-Control"]
    assert response["Content-Disposition"].startswith("inline")


def test_a_partial_download_is_still_an_attachment(client, founder, big):
    response = client.get(f"{LIST}/{big['id']}/file", HTTP_RANGE="bytes=0-99")
    assert response["Content-Disposition"].startswith("attachment")


# ── 8. the preview cut ───────────────────────────────────────────────────────
#
# A master is the wrong file to stream into a grid. The promo is 15.4 MB at
# 2148 kbps; measured against a real connection to the server — 0.25 MB/s
# down against the 0.26 MB/s it needs — it buffers, plays about twelve
# seconds and stalls. So video gets a lighter cut, built out of band.

import shutil
from django.core.management import call_command

needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None,
    reason="ffmpeg is not on this machine; the image installs it",
)


def test_a_video_is_queued_for_a_preview(client, founder):
    client.force_login(founder)
    body = upload(client, file=an_mp4(), title="A film").json()
    assert body["preview_state"] == "pending"


def test_an_image_needs_no_preview(client, founder):
    """A second copy of a 40 KB logo earns nothing."""
    client.force_login(founder)
    body = upload(client).json()
    assert body["preview_state"] == "not_needed"
    assert body["preview_url"] is not None


def test_a_pending_video_offers_no_preview_url(client, founder):
    """
    ⚠ IT MUST NOT FALL BACK TO THE MASTER.
    
    Serving the master while the cut is being built reproduces exactly the
    stall the cut exists to prevent — and it would look like an intermittent
    fault rather than a job that has not run yet.
    """
    client.force_login(founder)
    body = upload(client, file=an_mp4()).json()
    assert body["preview_url"] is None
    assert client.get(f"{LIST}/{body['id']}/preview").status_code == 404


def test_a_pending_video_can_still_be_downloaded(client, founder):
    """The master is there from the moment it is uploaded."""
    client.force_login(founder)
    body = upload(client, file=an_mp4()).json()
    assert client.get(f"{LIST}/{body['id']}/file").status_code == 200


def test_the_command_ignores_anything_that_is_not_video(client, founder):
    client.force_login(founder)
    pk = upload(client).json()["id"]
    MediaAsset.objects.filter(pk=pk).update(
        preview_state=MediaAsset.PreviewState.PENDING
    )
    call_command("build_media_previews")
    assert (
        MediaAsset.objects.get(pk=pk).preview_state
        == MediaAsset.PreviewState.NOT_NEEDED
    )


@needs_ffmpeg
def test_a_file_ffmpeg_cannot_read_fails_and_stops_being_retried(client, founder):
    """
    The fixture mp4 has a valid `ftyp` header and no actual video in it, so
    it passes the upload check and cannot be transcoded — which is exactly
    the case that must not retry forever. A file ffmpeg cannot read will not
    become readable, and a job retrying it every minute buries the failures
    worth looking at.
    """
    client.force_login(founder)
    pk = upload(client, file=an_mp4()).json()["id"]

    for _ in range(4):
        call_command("build_media_previews")

    asset = MediaAsset.objects.get(pk=pk)
    assert asset.preview_state == MediaAsset.PreviewState.FAILED
    assert asset.preview_attempts == 3          # MAX_ATTEMPTS, then left alone


@needs_ffmpeg
def test_a_failed_preview_never_serves_the_master_instead(client, founder):
    client.force_login(founder)
    pk = upload(client, file=an_mp4()).json()["id"]
    for _ in range(4):
        call_command("build_media_previews")

    assert MediaAsset.objects.get(pk=pk).preview_state == "failed"
    assert client.get(f"{LIST}/{pk}/preview").status_code == 404
    # ...but the original is still downloadable, which is what matters most.
    assert client.get(f"{LIST}/{pk}/file").status_code == 200


def test_the_attempt_is_counted_before_ffmpeg_runs(client, founder):
    """
    If the process dies mid-transcode the row must not come back looking
    untried — that is how one poison file becomes an infinite loop.
    """
    client.force_login(founder)
    pk = upload(client, file=an_mp4()).json()["id"]
    call_command("build_media_previews")
    assert MediaAsset.objects.get(pk=pk).preview_attempts >= 1
