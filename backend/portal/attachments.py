"""
Uploaded files: what we accept, and the single place one is ever served.

═══════════════════════════════════════════════════════════════════════════════
EVERY BYTE HERE CAME FROM OUTSIDE THE COMPANY.

A client photographs a booking sheet and sends it over WhatsApp; somebody
forwards a PDF. That is the point of the feature and it is also the whole
threat model. Two rules follow, and neither is negotiable:

  1. WE DECIDE WHAT A FILE IS, BY READING IT. The browser's Content-Type is a
     string the uploader chose. So is the extension. A file called photo.jpg
     claiming image/jpeg can be anything at all, and the only opinion worth
     having is the one formed by looking at the first few bytes.

  2. IT IS NEVER RENDERED IN OUR ORIGIN. Everything leaves through
     `AttachmentDownloadView` with Content-Disposition: attachment and
     X-Content-Type-Options: nosniff. An HTML or SVG file served inline from
     api.genmars.co.ke is stored cross-site scripting against whichever member
     of staff opens it — with their operations session attached.
═══════════════════════════════════════════════════════════════════════════════

There is no Caddy `file_server` for MEDIA_ROOT and there must not be one; see
the note in config/settings.py. A static route would bypass both rules at once
and there would be nothing in a code review to notice.
"""

from __future__ import annotations

from django.http import FileResponse, Http404

from operations.permissions import IsStaff
from rest_framework.views import APIView

from portal.models import ContactAttachment

# 10 MB. A phone photograph is 2–5 MB, a scanned PDF a little more, and
# anything past this is either a video or a mistake. Both are conversations to
# have with the sender rather than things to store.
MAX_BYTES = 10 * 1024 * 1024

# ── what we accept, keyed by the bytes a file actually starts with ───────────
#
# The value is the content type WE assign and the extension WE store it under.
# Neither comes from the upload.
#
# Deliberately short. Every entry is a format somebody here has an actual
# reason to receive, and the absent ones are the point: no SVG (it is a script
# container), no HTML, no archives, no Office macros. A client who needs to
# send one of those can send a PDF or a photo of it, and that conversation is
# cheaper than the class of bug this list closes.
SIGNATURES: list[tuple[bytes, str, str]] = [
    (b"\xff\xd8\xff", "image/jpeg", ".jpg"),
    (b"\x89PNG\r\n\x1a\n", "image/png", ".png"),
    (b"GIF87a", "image/gif", ".gif"),
    (b"GIF89a", "image/gif", ".gif"),
    (b"%PDF-", "application/pdf", ".pdf"),
]

# WEBP and HEIC need two checks — a container magic plus a brand further in —
# so they are handled separately rather than bent into the table above.
# HEIC matters: it is what an iPhone produces by default, and refusing it would
# mean refusing photographs from half the phones in the country.
def _container(head: bytes) -> tuple[str, str] | None:
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp", ".webp"
    if head[4:8] == b"ftyp" and head[8:12] in {
        b"heic", b"heix", b"hevc", b"hevx", b"mif1", b"msf1",
    }:
        return "image/heic", ".heic"
    return None


class AttachmentError(Exception):
    """A refusal the caller renders. Same shape as OperationsError."""

    def __init__(self, message: str, field: str | None = None):
        super().__init__(message)
        self.message = message
        self.field = field


def inspect(upload) -> tuple[str, str]:
    """
    Decide what this file is by reading it. Returns (content_type, extension).

    Raises AttachmentError with a message a person can act on — "we could not
    tell what this is" is useless to somebody holding a file that opens fine on
    their laptop, so the refusal names what we do take.
    """
    if upload.size == 0:
        raise AttachmentError("That file is empty.", field="file")
    if upload.size > MAX_BYTES:
        mb = MAX_BYTES // (1024 * 1024)
        raise AttachmentError(
            f"That file is {upload.size / (1024 * 1024):.1f} MB and the limit is "
            f"{mb} MB. Send a photo of the page rather than the whole scan, or "
            "put it somewhere and paste the link into the note.",
            field="file",
        )

    head = upload.read(16)
    # Rewind, or the saved file is missing its first sixteen bytes and every
    # image is silently corrupt.
    upload.seek(0)

    for magic, content_type, extension in SIGNATURES:
        if head.startswith(magic):
            return content_type, extension

    container = _container(head)
    if container is not None:
        return container

    raise AttachmentError(
        "That is not a file type we take. Photographs (JPEG, PNG, HEIC, WebP, "
        "GIF) and PDFs — everything else has to arrive as one of those. It is "
        "not about the name: we check what the file actually is.",
        field="file",
    )


class AttachmentDownloadView(APIView):
    """
    The ONLY way a stored file leaves this system.

    ── STAFF ONLY, AND THAT IS NOT AN OVERSIGHT ────────────────────────────────

    These hang off ContactLogEntry, which is internal — see its docstring. A
    client-facing route here would make the contact log client-visible through
    the back door, and the log is written honestly precisely because nobody
    outside Genmars reads it.

    ── WHY THE HEADERS MATTER MORE THAN THE PERMISSION ─────────────────────────

    The permission stops the wrong person reading a file. The headers stop the
    RIGHT person being attacked by one. `as_attachment=True` means the browser
    saves rather than renders, and `nosniff` stops it second-guessing the type
    we assigned — without which a PDF that is really HTML executes in our
    origin, with an operations session in the cookie jar.
    """

    permission_classes = [IsStaff]

    def get(self, request, pk: int):
        attachment = (
            ContactAttachment.objects.select_related("entry").filter(pk=pk).first()
        )
        if attachment is None:
            raise Http404

        try:
            handle = attachment.file.open("rb")
        except FileNotFoundError:
            # The row outlived the file — a restore from a database dump, which
            # does not carry MEDIA_ROOT. Saying so is better than a 500, and
            # far better than an empty download that looks like corruption.
            raise Http404("The record exists but the file is not on this server.")

        response = FileResponse(
            handle,
            content_type=attachment.content_type,
            as_attachment=True,
            filename=attachment.original_name,
        )
        response["X-Content-Type-Options"] = "nosniff"
        # Belt and braces: even if something downstream ever flips the
        # disposition, this stops the document scripting anything.
        response["Content-Security-Policy"] = "default-src 'none'; sandbox"
        # A client's document must not sit in a shared proxy cache.
        response["Cache-Control"] = "private, no-store"
        return response
