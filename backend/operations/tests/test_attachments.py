"""
Uploaded files, and opening an order for a client we already have.

═══════════════════════════════════════════════════════════════════════════════
THE TESTS THAT MATTER HERE ARE THE THREE ABOUT WHAT A FILE IS AND HOW IT LEAVES.

Every byte in this feature came from outside the company. A file we accept
because the browser said it was a JPEG, or serve inline because it says .pdf,
is stored cross-site scripting against whoever on this team opens it — with
their operations session attached.
═══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import Membership, Organisation, User
from portal import attachments as rules
from portal.models import (
    ActivityLog,
    ContactAttachment,
    ContactLogEntry,
    Notification,
    Order,
)

pytestmark = pytest.mark.django_db

PASSWORD = "correct-horse-battery"

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
PDF = b"%PDF-1.7\n" + b"\x00" * 64
HEIC = b"\x00\x00\x00\x18ftypheic" + b"\x00" * 64
WEBP = b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 64


@pytest.fixture(autouse=True)
def media(tmp_path):
    """
    Every test writes into its own directory and it is thrown away after.

    Without this the suite scatters files through the real MEDIA_ROOT and the
    tests start depending on what a previous run left behind.
    """
    with override_settings(MEDIA_ROOT=str(tmp_path)):
        yield


@pytest.fixture
def staff() -> User:
    return User.objects.create_user(
        email="ops@genmars.co.ke",
        password=PASSWORD,
        full_name="Ops Person",
        is_staff=True,
        staff_role=User.StaffRole.FOUNDER,
        email_verified_at=timezone.now(),
    )


@pytest.fixture
def spa() -> Organisation:
    return Organisation.objects.create(name="Clips Serenity Spa")


@pytest.fixture
def entry(staff, spa) -> ContactLogEntry:
    return ContactLogEntry.objects.create(
        organisation=spa,
        channel=ContactLogEntry.Channel.WHATSAPP,
        direction=ContactLogEntry.Direction.INBOUND,
        summary="Sent a photo of the paper booking sheet",
        recorded_by=staff,
        recorded_by_label=staff.full_name,
    )


def _upload(client, entry, content: bytes, name: str, declared="image/jpeg", caption=""):
    return client.post(
        reverse("ops-contact-attachments", args=[entry.pk]),
        {"file": SimpleUploadedFile(name, content, content_type=declared), "caption": caption},
    )


# ── what we accept ───────────────────────────────────────────────────────────


def test_a_photo_is_attached_to_the_conversation(client, staff, entry):
    client.force_login(staff)
    response = _upload(client, entry, JPEG, "booking-sheet.jpg", caption="Their paper diary")

    assert response.status_code == 201
    body = response.json()
    assert body["original_name"] == "booking-sheet.jpg"
    assert body["content_type"] == "image/jpeg"
    assert body["is_image"] is True
    assert body["caption"] == "Their paper diary"
    assert body["uploaded_by"] == "Ops Person"
    # The download route, never a media path.
    assert body["url"] == f"/api/attachments/{body['id']}"


@pytest.mark.parametrize(
    "content,expected",
    [(JPEG, "image/jpeg"), (PNG, "image/png"), (PDF, "application/pdf"),
     (HEIC, "image/heic"), (WEBP, "image/webp")],
)
def test_the_formats_a_kenyan_client_actually_sends(client, staff, entry, content, expected):
    """HEIC in particular: it is what an iPhone produces by default, and
    refusing it means refusing photographs from half the phones in the country."""
    client.force_login(staff)
    response = _upload(client, entry, content, "thing.bin", declared="application/octet-stream")
    assert response.status_code == 201
    assert response.json()["content_type"] == expected


def test_the_type_comes_from_the_bytes_not_from_the_name_or_the_header(client, staff, entry):
    """
    ═══════════════════════════════════════════════════════════════════════════
    Both the filename and the Content-Type are strings the uploader chose.

    Here an executable is called photo.jpg and declares image/jpeg. Believing
    either would store it as an image and hand it back as one.
    ═══════════════════════════════════════════════════════════════════════════
    """
    client.force_login(staff)
    response = _upload(client, entry, b"MZ\x90\x00" + b"\x00" * 64, "photo.jpg", "image/jpeg")

    assert response.status_code == 400
    assert response.json()["field"] == "file"
    assert not ContactAttachment.objects.exists()


def test_an_svg_is_refused_however_it_is_labelled(client, staff, entry):
    """
    SVG is a script container. It is absent from the allowlist on purpose, and
    calling it .png does not get it in.
    """
    client.force_login(staff)
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    assert _upload(client, entry, svg, "diagram.png", "image/png").status_code == 400
    assert _upload(client, entry, svg, "diagram.svg", "image/svg+xml").status_code == 400


def test_html_is_refused(client, staff, entry):
    client.force_login(staff)
    page = b"<!doctype html><script>fetch('/api/ops/staff')</script>"
    assert _upload(client, entry, page, "invoice.pdf", "application/pdf").status_code == 400


def test_a_file_too_large_is_refused_with_something_to_do_about_it(client, staff, entry):
    client.force_login(staff)
    response = _upload(client, entry, JPEG + b"\x00" * (rules.MAX_BYTES + 1), "huge.jpg")

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "10 MB" in detail
    # A refusal that only says no leaves somebody holding a file that opens
    # fine on their laptop.
    assert "photo of the page" in detail


def test_an_empty_file_is_refused(client, staff, entry):
    client.force_login(staff)
    assert _upload(client, entry, b"", "nothing.jpg").status_code == 400


def test_no_file_at_all_is_a_refusal_not_a_crash(client, staff, entry):
    client.force_login(staff)
    response = client.post(reverse("ops-contact-attachments", args=[entry.pk]), {})
    assert response.status_code == 400
    assert response.json()["field"] == "file"


def test_the_first_bytes_survive_the_sniff(client, staff, entry):
    """
    `inspect` reads the head to identify the file and must rewind. Without the
    seek(0) every stored image is missing sixteen bytes and silently corrupt —
    which nothing else in this suite would notice.
    """
    client.force_login(staff)
    payload = JPEG + b"the rest of the file"
    attachment_id = _upload(client, entry, payload, "photo.jpg").json()["id"]

    attachment = ContactAttachment.objects.get(pk=attachment_id)
    assert attachment.file.read() == payload
    assert attachment.size_bytes == len(payload)


# ── where it is stored ───────────────────────────────────────────────────────


def test_the_stored_path_never_contains_the_clients_filename(client, staff, entry):
    """
    It is attacker-controlled, it ends up in logs and backups, and as a path it
    is a traversal. What they called it is display text.
    """
    client.force_login(staff)
    nasty = "../../../etc/passwd"
    attachment_id = _upload(client, entry, JPEG, nasty).json()["id"]

    attachment = ContactAttachment.objects.get(pk=attachment_id)
    assert ".." not in attachment.file.name
    assert "passwd" not in attachment.file.name
    assert attachment.file.name.endswith(".jpg")
    assert f"contact/{entry.organisation_id}/" in attachment.file.name


def test_a_windows_path_is_reduced_to_a_name(client, staff, entry):
    client.force_login(staff)
    body = _upload(client, entry, JPEG, r"C:\Users\Asha\Pictures\sheet.jpg").json()
    assert body["original_name"] == "sheet.jpg"


def test_the_extension_comes_from_the_bytes(client, staff, entry):
    """A PDF uploaded as .jpg is stored as .pdf, because that is what it is."""
    client.force_login(staff)
    attachment_id = _upload(client, entry, PDF, "scan.jpg", "image/jpeg").json()["id"]
    assert ContactAttachment.objects.get(pk=attachment_id).file.name.endswith(".pdf")


# ── how it leaves ────────────────────────────────────────────────────────────


def test_a_file_is_only_ever_sent_as_a_download(client, staff, entry):
    """
    ═══════════════════════════════════════════════════════════════════════════
    THE HEADERS MATTER MORE THAN THE PERMISSION DOES.

    The permission stops the wrong person reading a file. These stop the RIGHT
    person being attacked by one: rendered inline from our origin, an uploaded
    document runs with an operations session in the cookie jar.
    ═══════════════════════════════════════════════════════════════════════════
    """
    client.force_login(staff)
    attachment_id = _upload(client, entry, PDF, "quote.pdf", "application/pdf").json()["id"]

    response = client.get(reverse("attachment-download", args=[attachment_id]))
    assert response.status_code == 200
    assert response["Content-Disposition"].startswith("attachment")
    assert response["X-Content-Type-Options"] == "nosniff"
    assert "sandbox" in response["Content-Security-Policy"]
    assert "no-store" in response["Cache-Control"]
    assert b"".join(response.streaming_content) == PDF


def test_a_client_account_cannot_download_an_attachment(client, staff, entry, spa):
    """
    These hang off the contact log, which is internal. A client-facing route
    here would make that log client-visible through the back door.
    """
    client.force_login(staff)
    attachment_id = _upload(client, entry, JPEG, "sheet.jpg").json()["id"]
    client.logout()

    member = User.objects.create_user(
        email="owner@spa.co.ke", password=PASSWORD, email_verified_at=timezone.now()
    )
    Membership.objects.create(user=member, organisation=spa)
    client.force_login(member)

    assert client.get(reverse("attachment-download", args=[attachment_id])).status_code == 403


def test_an_anonymous_request_gets_nothing(client, staff, entry):
    client.force_login(staff)
    attachment_id = _upload(client, entry, JPEG, "sheet.jpg").json()["id"]
    client.logout()
    assert client.get(reverse("attachment-download", args=[attachment_id])).status_code == 403


def test_a_missing_file_is_a_404_rather_than_a_500(client, staff, entry):
    """
    The row outlives the file after a restore from a database dump, which does
    not carry MEDIA_ROOT. Saying so beats a stack trace, and beats an empty
    download that reads as corruption.
    """
    client.force_login(staff)
    attachment_id = _upload(client, entry, JPEG, "sheet.jpg").json()["id"]

    attachment = ContactAttachment.objects.get(pk=attachment_id)
    attachment.file.storage.delete(attachment.file.name)

    assert client.get(reverse("attachment-download", args=[attachment_id])).status_code == 404


def test_deleting_an_attachment_removes_the_bytes(client, staff, entry):
    """
    A real delete, unlike almost everything else here. A client who sends the
    wrong document by mistake is owed its actually being gone, and a soft
    delete would be us saying it was removed when it was not.
    """
    client.force_login(staff)
    attachment_id = _upload(client, entry, JPEG, "wrong-document.jpg").json()["id"]
    attachment = ContactAttachment.objects.get(pk=attachment_id)
    storage, name = attachment.file.storage, attachment.file.name
    assert storage.exists(name)

    assert client.delete(reverse("ops-attachment", args=[attachment_id])).status_code == 204

    assert not ContactAttachment.objects.filter(pk=attachment_id).exists()
    assert not storage.exists(name)
    # The fact that it existed, and who removed it, survives.
    assert ActivityLog.objects.filter(summary__contains="Attachment removed").exists()


def test_attachments_ride_along_with_the_conversation(client, staff, entry, spa):
    client.force_login(staff)
    _upload(client, entry, JPEG, "sheet.jpg", caption="Their paper diary")

    body = client.get(reverse("ops-client", args=[spa.pk])).json()
    assert body["contact_log"][0]["attachments"][0]["caption"] == "Their paper diary"


def test_the_client_export_never_carries_an_attachment(client, staff, entry, spa):
    """Same rule as the contact log itself — see test_client_record.py."""
    client.force_login(staff)
    _upload(client, entry, JPEG, "internal-note.jpg", caption="Their paper diary")
    client.logout()

    member = User.objects.create_user(
        email="owner@spa.co.ke", password=PASSWORD, email_verified_at=timezone.now()
    )
    Membership.objects.create(user=member, organisation=spa)
    client.force_login(member)

    export = client.get(reverse("export")).content.decode()
    assert "internal-note" not in export
    assert "paper diary" not in export


# ── opening an order for a client we already have ────────────────────────────


def _order(client, org, **overrides):
    body = {
        "title": "Online booking",
        "scope": "Add online booking to the existing site, with SMS confirmation.",
        "exclusions": "Payments. Staff rostering.",
    }
    body.update(overrides)
    return client.post(
        reverse("ops-client-orders", args=[org.pk]), body, content_type="application/json"
    )


def test_an_order_can_be_opened_for_an_existing_client(client, staff, spa):
    client.force_login(staff)
    response = _order(client, spa)

    assert response.status_code == 201
    body = response.json()
    assert body["reference"].startswith("GM-")
    # SCOPING, not active. Charter 02 §I — a signed statement of work comes
    # before delivery, and this is a written record of what was asked for.
    assert Order.objects.get(reference=body["reference"]).status == Order.Status.SCOPING


def test_an_order_needs_a_scope_in_writing(client, staff, spa):
    client.force_login(staff)
    response = _order(client, spa, scope="   ")
    assert response.status_code == 400
    assert response.json()["field"] == "scope"


def test_the_client_is_told_in_the_dashboard_and_by_email(client, staff, spa, mailoutbox):
    owner = User.objects.create_user(
        email="owner@spa.co.ke",
        password=PASSWORD,
        full_name="The owner",
        email_verified_at=timezone.now(),
    )
    Membership.objects.create(user=owner, organisation=spa, receives_updates=True)

    client.force_login(staff)
    reference = _order(client, spa).json()["reference"]

    notification = Notification.objects.get(user=owner)
    assert notification.url == f"/dashboard/{reference}"

    assert len(mailoutbox) == 1
    assert reference in mailoutbox[0].subject


def test_the_email_does_not_claim_work_has_started(client, staff, spa, mailoutbox):
    """
    ═══════════════════════════════════════════════════════════════════════════
    Charter 02 §I — a signed statement of work comes before delivery.

    This email goes out on an order in SCOPING, often minutes after a phone
    call. "We've started on your booking system" would commit the company by
    notification instead of by contract.
    ═══════════════════════════════════════════════════════════════════════════
    """
    owner = User.objects.create_user(
        email="owner@spa.co.ke", password=PASSWORD, email_verified_at=timezone.now()
    )
    Membership.objects.create(user=owner, organisation=spa, receives_updates=True)

    client.force_login(staff)
    _order(client, spa)

    body = mailoutbox[0].body.lower()
    assert "nothing has started yet" in body
    for claim in ("we have begun", "we've started", "work has started", "in progress"):
        assert claim not in body

    # The scope and the exclusions are IN the message — the point of writing
    # scope down is that the client can disagree while it is cheap.
    assert "sms confirmation" in body
    assert "staff rostering" in body


def test_empty_exclusions_are_stated_rather_than_dropped(client, staff, spa, mailoutbox):
    """
    A blank exclusions field means "we have not said what is out of scope",
    which is worth the client seeing. Dropping the heading would let an
    unstated boundary look like a settled one.
    """
    owner = User.objects.create_user(
        email="owner@spa.co.ke", password=PASSWORD, email_verified_at=timezone.now()
    )
    Membership.objects.create(user=owner, organisation=spa, receives_updates=True)

    client.force_login(staff)
    _order(client, spa, exclusions="")

    assert "not yet written down what is outside this" in mailoutbox[0].body


def test_nobody_is_emailed_who_asked_not_to_be_or_never_verified(client, staff, spa, mailoutbox):
    unsubscribed = User.objects.create_user(
        email="quiet@spa.co.ke", password=PASSWORD, email_verified_at=timezone.now()
    )
    Membership.objects.create(user=unsubscribed, organisation=spa, receives_updates=False)

    never_accepted = User.objects.create_user(email="ghost@spa.co.ke", password=PASSWORD)
    Membership.objects.create(user=never_accepted, organisation=spa, receives_updates=True)

    client.force_login(staff)
    _order(client, spa)

    assert mailoutbox == []


def test_an_order_can_be_opened_without_telling_them_yet(client, staff, spa, mailoutbox):
    owner = User.objects.create_user(
        email="owner@spa.co.ke", password=PASSWORD, email_verified_at=timezone.now()
    )
    Membership.objects.create(user=owner, organisation=spa, receives_updates=True)

    client.force_login(staff)
    assert _order(client, spa, tell_client=False).status_code == 201

    assert mailoutbox == []
    assert not Notification.objects.filter(user=owner).exists()


def test_opening_an_order_from_a_conversation_links_the_two(client, staff, spa, entry):
    client.force_login(staff)
    reference = _order(client, spa, from_contact=entry.pk).json()["reference"]

    entry.refresh_from_db()
    assert entry.order.reference == reference


def test_an_order_cannot_be_opened_from_another_clients_conversation(client, staff, spa, entry):
    other = Organisation.objects.create(name="Somebody Else Ltd")
    client.force_login(staff)
    assert _order(client, other, from_contact=entry.pk).status_code == 400


def test_opening_an_order_is_a_commercial_decision(client, spa):
    """
    Charter 02 §I — qualification belongs to the commercial partners. A
    delivery engineer opening one would commit the company's capacity.
    """
    engineer = User.objects.create_user(
        email="dev@genmars.co.ke",
        password=PASSWORD,
        is_staff=True,
        staff_role=User.StaffRole.DELIVERY,
        email_verified_at=timezone.now(),
    )
    client.force_login(engineer)
    assert _order(client, spa).status_code == 403
    assert not Order.objects.exists()


def test_the_order_gets_its_delivery_gates(client, staff, spa):
    """Same as a converted enquiry. An order without gates is invisible to the
    delivery board, which is where it is actually worked."""
    client.force_login(staff)
    reference = _order(client, spa).json()["reference"]
    assert Order.objects.get(reference=reference).gates.count() > 0
