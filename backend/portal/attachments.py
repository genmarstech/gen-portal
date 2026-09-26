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

# The company's own filing cabinet gets more room. A signed contract pack
# scanned at 300dpi runs past 10 MB regularly, and unlike a client's upload
# there is nobody to have the conversation with — it is our own paperwork and
# it is the size it is.
LIBRARY_MAX_BYTES = 25 * 1024 * 1024

# The media shelf holds video. A sixty-second 1080p promo is ~17 MB, a longer
# cut or a raw phone clip is several times that, and the point of keeping it
# is that somebody can pull the original down and edit it. A cap that forces
# people to compress first defeats the feature.
#
# Django writes anything past FILE_UPLOAD_MAX_MEMORY_SIZE (2 MB) straight to
# a temp file, so a 200 MB upload never sits in the container's memory.
MEDIA_MAX_BYTES = 200 * 1024 * 1024

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
#
# This is the list for files arriving from OUTSIDE. The company library passes
# `documents=True` and gets office formats as well — see `_office` below for
# why that is a different question rather than a relaxation of this one.
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


# ── office documents, which are ZIP files wearing a hat ──────────────────────
#
# ═════════════════════════════════════════════════════════════════════════════
# THESE ARE ACCEPTED FOR THE COMPANY LIBRARY AND NOWHERE ELSE, AND THE REASON
# IS NOT THAT THEY BECAME SAFE.
#
# `.docx` and friends start with `PK\x03\x04` — the same four bytes as any zip,
# a JAR, an APK, or a zip bomb. Rule 1 at the top of this file says we decide
# what a file is by reading it, and for these that means OPENING THE CONTAINER
# and looking at what is inside, because the magic number alone identifies
# nothing. An allowlist entry keyed on `PK` would be an allowlist for arbitrary
# archives, which is worse than having no entry at all.
#
# So: read the central directory — `namelist()` does not decompress anything,
# so a bomb never expands — and require the part layout the format mandates.
#
# WHAT THIS DOES NOT CLAIM. A `.docx` that passes here is a real Word document.
# It is not a SAFE one, and nothing in this file could make it so: a document
# can carry a remote-template link or a nasty embedded object and still be
# structurally perfect. What keeps that from mattering is the same thing that
# keeps it from mattering for a PDF — `AttachmentDownloadView` serves every
# byte with `as_attachment`, `nosniff` and a sandbox CSP, so nothing here is
# ever rendered in our origin. The residual risk is somebody opening our own
# paperwork on their own laptop, which is the risk of email.
#
# Macros ARE refused, because they are the one thing we can identify and the
# one thing with no legitimate place in a filing cabinet.
# ═════════════════════════════════════════════════════════════════════════════

# A required member directory → what we call the format and store it under.
OOXML_PARTS: list[tuple[str, str, str]] = [
    ("word/", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", ".docx"),
    ("xl/", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ".xlsx"),
    ("ppt/", "application/vnd.openxmlformats-officedocument.presentationml.presentation", ".pptx"),
]

# OpenDocument declares itself properly: the first entry is an uncompressed
# `mimetype` member holding exactly this string. Accepted because refusing it
# would be arbitrary — LibreOffice is what half the machines here run, and
# "save it as Word first" is a rule with no security behind it.
ODF_TYPES: dict[bytes, tuple[str, str]] = {
    b"application/vnd.oasis.opendocument.text": (
        "application/vnd.oasis.opendocument.text", ".odt",
    ),
    b"application/vnd.oasis.opendocument.spreadsheet": (
        "application/vnd.oasis.opendocument.spreadsheet", ".ods",
    ),
    b"application/vnd.oasis.opendocument.presentation": (
        "application/vnd.oasis.opendocument.presentation", ".odp",
    ),
}

# A container with more members than any real document has is not a document.
# Bounds the work done on a hostile central directory; a large Word file with
# many images sits in the low hundreds.
MAX_ARCHIVE_MEMBERS = 4096


# ── moving pictures ─────────────────────────────────────────────────────────
#
# ═════════════════════════════════════════════════════════════════════════════
# VIDEO IS ACCEPTED FOR THE MEDIA SHELF AND NOWHERE ELSE.
#
# MP4 and friends are ISO base media files: the bytes at offset 4 are `ftyp`
# and the four after that are the brand. Same shape as the HEIC check above —
# HEIC is literally a member of the same family — so this is the same kind of
# identification, not a weaker one.
#
# WebM is Matroska and announces itself with an EBML header instead.
#
# What this does NOT do is parse the container. Nothing here walks atoms,
# reads a moov, or decodes a frame; a demuxer running on uploaded bytes is
# exactly the attack surface portal/attachments.py exists to avoid. We
# identify the format and store the file. The browser decodes it, in a
# sandbox, having been told by `nosniff` exactly what it is.
# ═════════════════════════════════════════════════════════════════════════════

# ISO base media brands worth taking. Deliberately not `ftyp` alone: that
# would accept every ISO-BMFF ever specified, including formats nothing here
# can play.
VIDEO_BRANDS: dict[bytes, tuple[str, str]] = {
    b"isom": ("video/mp4", ".mp4"),
    b"iso2": ("video/mp4", ".mp4"),
    b"iso4": ("video/mp4", ".mp4"),
    b"iso5": ("video/mp4", ".mp4"),
    b"iso6": ("video/mp4", ".mp4"),
    b"mp41": ("video/mp4", ".mp4"),
    b"mp42": ("video/mp4", ".mp4"),
    b"avc1": ("video/mp4", ".mp4"),
    b"mmp4": ("video/mp4", ".mp4"),
    b"M4V ": ("video/mp4", ".m4v"),
    b"qt  ": ("video/quicktime", ".mov"),
}


def _video(head: bytes) -> tuple[str, str] | None:
    """Identify a video by its container header. No demuxing — see the banner."""
    if head[4:8] == b"ftyp":
        return VIDEO_BRANDS.get(head[8:12])
    # EBML magic. Both .webm and .mkv start with it; we serve it as WebM,
    # which is what a browser will actually try to play.
    if head[:4] == b"\x1a\x45\xdf\xa3":
        return "video/webm", ".webm"
    return None


def _office(upload) -> tuple[str, str] | None:
    """
    Decide whether a zip container is an office document, by opening it.

    Returns None for anything that is merely a zip — the caller then refuses it
    with the ordinary "not a type we take" message, which is the correct answer
    for a `.zip`, a `.jar` and an `.apk` alike.

    Raises AttachmentError only for a macro-enabled document, because that is a
    recognised document being turned away for a specific reason and the person
    holding it deserves to be told which.
    """
    import zipfile

    upload.seek(0)
    try:
        with zipfile.ZipFile(upload) as archive:
            names = archive.namelist()
            if len(names) > MAX_ARCHIVE_MEMBERS:
                return None
            members = set(names)

            # OpenDocument, checked first: its `mimetype` member is definitive
            # where the OOXML check below is structural.
            if "mimetype" in members:
                try:
                    declared = archive.read("mimetype")[:128].strip()
                except (zipfile.BadZipFile, OSError, RuntimeError):
                    return None
                return ODF_TYPES.get(declared)
    except (zipfile.BadZipFile, OSError, EOFError):
        # Not a readable zip. Truncated uploads land here too, and "we could not
        # tell what this is" is the honest answer for both.
        return None
    finally:
        # Whatever happened, the next reader — and the eventual save — must get
        # the file from the beginning. Forgetting this stores a headless file.
        upload.seek(0)

    # Every OOXML document has this; its absence means the zip is something
    # else wearing a .docx name.
    if "[Content_Types].xml" not in members:
        return None

    # The one thing we can identify and will not keep. `vbaProject.bin` is
    # where the macro lives in every OOXML variant, whatever the extension
    # says — a `.docm` renamed to `.docx` is caught here, which is the point.
    if any(name.endswith("vbaProject.bin") for name in members):
        raise AttachmentError(
            "That document contains macros, and the library does not keep those. "
            "Save it again without the macros, or export it as a PDF.",
            field="file",
        )

    for prefix, content_type, extension in OOXML_PARTS:
        if any(name.startswith(prefix) for name in members):
            return content_type, extension
    return None


class AttachmentError(Exception):
    """A refusal the caller renders. Same shape as OperationsError."""

    def __init__(self, message: str, field: str | None = None):
        super().__init__(message)
        self.message = message
        self.field = field


def inspect(
    upload,
    *,
    documents: bool = False,
    media: bool = False,
    max_bytes: int | None = None,
) -> tuple[str, str]:
    """
    Decide what this file is by reading it. Returns (content_type, extension).

    Raises AttachmentError with a message a person can act on — "we could not
    tell what this is" is useless to somebody holding a file that opens fine on
    their laptop, so the refusal names what we do take.

    `documents=True` additionally accepts office formats, and is passed by the
    company library alone. `media=True` additionally accepts video, and is
    passed by the media shelf alone. Both default to False so that adding a
    surface could not quietly widen what a CLIENT may upload to the contact
    log — the surfaces answer to different threat models and the default is
    the strictest one.
    """
    limit = MAX_BYTES if max_bytes is None else max_bytes

    if upload.size == 0:
        raise AttachmentError("That file is empty.", field="file")
    if upload.size > limit:
        mb = limit // (1024 * 1024)
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

    # Checked before the office branch: a video shares the `ftyp` shape with
    # HEIC above, and is nothing like a zip.
    if media:
        video = _video(head)
        if video is not None:
            return video

    # Checked last and only when asked. `_office` opens the archive, which is
    # real work, and there is no reason to do it for a file that already
    # matched a signature above.
    if documents and head.startswith(b"PK\x03\x04"):
        office = _office(upload)
        if office is not None:
            return office
        raise AttachmentError(
            "That is a zip archive rather than a document. The library takes "
            "Word, Excel, PowerPoint and OpenDocument files, PDFs and "
            "photographs — send what is inside it rather than the archive.",
            field="file",
        )

    if media:
        accepted = (
            "Video (MP4, MOV, WebM), photographs (JPEG, PNG, HEIC, WebP, GIF) "
            "and PDFs"
        )
    elif documents:
        accepted = (
            "Word, Excel, PowerPoint and OpenDocument files, PDFs and "
            "photographs (JPEG, PNG, HEIC, WebP, GIF)"
        )
    else:
        accepted = "Photographs (JPEG, PNG, HEIC, WebP, GIF) and PDFs"
    raise AttachmentError(
        f"That is not a file type we take. {accepted} — everything else has to "
        "arrive as one of those. It is not about the name: we check what the "
        "file actually is.",
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


# ── how big a picture is, without decoding it ───────────────────────────────
#
# ═════════════════════════════════════════════════════════════════════════════
# THIS READS A HEADER. IT IS NOT AN IMAGE PARSER, AND THE DISTINCTION IS THE
# WHOLE REASON IT IS ALLOWED TO EXIST.
#
# ContactAttachment's docstring refuses Pillow and says why: an image parser
# running on files from outside is historically one of the most exploited
# pieces of code in any stack, and thumbnails were not worth it.
#
# What follows decodes nothing. It reads a handful of big-endian integers
# from fixed offsets — the same operation as reading a file's length — and
# never touches compressed pixel data. There is no allocation proportional to
# the image, no decompression, and no third-party code.
#
# It exists because a media grid without aspect ratios reflows as every tile
# loads. Dimensions are COSMETIC: every branch fails soft and returns None,
# and the caller stores nulls. Nothing downstream may depend on them.
# ═════════════════════════════════════════════════════════════════════════════


def _be(b: bytes) -> int:
    return int.from_bytes(b, "big")


def dimensions(upload) -> tuple[int, int] | None:
    """(width, height) for a raster image, or None. Never raises."""
    try:
        upload.seek(0)
        head = upload.read(32)

        # PNG: IHDR is always the first chunk, width and height at 16..24.
        if head[:8] == b"\x89PNG\r\n\x1a\n" and head[12:16] == b"IHDR":
            return _be(head[16:20]), _be(head[20:24])

        # GIF: little-endian, at a fixed offset.
        if head[:6] in (b"GIF87a", b"GIF89a"):
            return (
                int.from_bytes(head[6:8], "little"),
                int.from_bytes(head[8:10], "little"),
            )

        # WebP: three sub-formats, each with its size in a different place.
        if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
            chunk = head[12:16]
            if chunk == b"VP8X":
                # 24-bit little-endian, stored as value-1.
                return (
                    int.from_bytes(head[24:27], "little") + 1,
                    int.from_bytes(head[27:30], "little") + 1,
                )
            if chunk == b"VP8 ":
                return (
                    int.from_bytes(head[26:28], "little") & 0x3FFF,
                    int.from_bytes(head[28:30], "little") & 0x3FFF,
                )
            if chunk == b"VP8L":
                bits = int.from_bytes(head[21:25], "little")
                return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
            return None

        # JPEG: the only one needing a walk, because the frame header sits
        # after a variable number of segments. BOUNDED to 64 hops — a crafted
        # file must not be able to spin this, and no real photograph has
        # anywhere near that many segments before its SOF.
        if head[:3] == b"\xff\xd8\xff":
            upload.seek(2)
            for _ in range(64):
                marker = upload.read(2)
                if len(marker) < 2 or marker[0] != 0xFF:
                    return None
                kind = marker[1]
                length = _be(upload.read(2))
                if length < 2:
                    return None
                # SOF0..SOF15, excluding the four that are not frame headers.
                if 0xC0 <= kind <= 0xCF and kind not in (0xC4, 0xC8, 0xCC):
                    body = upload.read(5)
                    if len(body) < 5:
                        return None
                    return _be(body[3:5]), _be(body[1:3])
                upload.seek(length - 2, 1)
            return None

        return None
    except (OSError, ValueError, IndexError):
        # Cosmetic. A file we cannot measure is still a file we can store.
        return None
    finally:
        try:
            upload.seek(0)
        except OSError:
            pass
