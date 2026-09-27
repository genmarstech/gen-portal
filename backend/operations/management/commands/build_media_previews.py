"""
Turn uploaded video into something a browser can actually stream.

═══════════════════════════════════════════════════════════════════════════════
THIS RUNS OUT OF BAND, AND THAT IS THE POINT.

ffmpeg is a demuxer and a pile of decoders pointed at a file somebody
uploaded. backend/Dockerfile carries the full argument for why it is in the
image at all; the part that matters here is that it must never run inside a
request. A crash takes out a batch job instead of an API worker, a hang is
bounded by the timeout below instead of holding a gthread slot, and a
malformed file fails one row rather than a page load.

WHY NOT CELERY. It is in requirements.txt and has been for the life of this
repository — unimported, with no worker container and no broker wiring. Using
it here would mean standing up a worker, a second thing to deploy, monitor
and restart, for a job that runs a few times a week. There are already five
systemd timers on this box doing work of exactly this shape; this is the
sixth. See deploy/genmars-portal-previews.{service,timer}.
═══════════════════════════════════════════════════════════════════════════════

    docker compose exec api python manage.py build_media_previews
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from django.core.files import File
from django.core.management.base import BaseCommand

from portal.models import MediaAsset

# Fits comfortably inside the bandwidth measured to this server (0.25 MB/s)
# with headroom to spare: CRF 26 at this size lands around 800 kbps, or
# 0.10 MB/s. The master is untouched and stays CRF 18.
CRF = "26"

# A 1280 box on the long edge. 1080x1920 becomes 720x1280, 1920x1080 becomes
# 1280x720 — ample for a grid tile and for the full-screen preview somebody
# opens to check a cut. `force_divisible_by=2` because H.264 needs even
# dimensions and odd ones fail at the encoder with an unhelpful message.
SCALE = "scale=w=1280:h=1280:force_original_aspect_ratio=decrease:force_divisible_by=2"

# Generous for a minute of video, tight enough that a pathological file
# cannot occupy the box. Roughly ten minutes.
TIMEOUT_SECONDS = 600

# After this many failures the row is left alone. A file ffmpeg cannot read
# will not become readable, and retrying it every minute forever buries the
# failures worth looking at.
MAX_ATTEMPTS = 3


class Command(BaseCommand):
    help = "Build the lighter preview cut for any video that lacks one."

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=int,
            default=3,
            help="How many to transcode in one run. Keeps a backlog from "
            "occupying the box for an hour.",
        )
        parser.add_argument(
            "--asset",
            type=int,
            help="Rebuild one asset by id, whatever state it is in.",
        )

    def handle(self, *args, **options):
        if options.get("asset"):
            queue = list(MediaAsset.objects.filter(pk=options["asset"]))
            if not queue:
                self.stderr.write(f"No asset {options['asset']}.")
                return
        else:
            queue = list(
                MediaAsset.objects.filter(
                    preview_state=MediaAsset.PreviewState.PENDING,
                    preview_attempts__lt=MAX_ATTEMPTS,
                ).order_by("created_at")[: options["limit"]]
            )

        if not queue:
            self.stdout.write("Nothing waiting.")
            return

        for asset in queue:
            self._build(asset)

    def _build(self, asset: MediaAsset) -> None:
        if not asset.is_video:
            # Images serve themselves; nothing to do and nothing to record.
            asset.preview_state = MediaAsset.PreviewState.NOT_NEEDED
            asset.save(update_fields=["preview_state", "updated_at"])
            return

        # Count the attempt BEFORE trying. If ffmpeg wedges the box hard
        # enough to lose this process, the row must not come back looking
        # untried — that is how a poison file becomes an infinite loop.
        asset.preview_attempts += 1
        asset.save(update_fields=["preview_attempts", "updated_at"])

        self.stdout.write(
            f"#{asset.id} {asset.title} "
            f"({asset.size_bytes / 1048576:.1f} MB, attempt {asset.preview_attempts})"
        )

        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source"
            output = Path(tmp) / "preview.mp4"

            # Copied out of storage rather than handed a path: the storage
            # backend is not required to expose one, and a local path is the
            # kind of assumption that breaks the day this moves off disk.
            with asset.file.open("rb") as src, source.open("wb") as dst:
                for chunk in iter(lambda: src.read(1024 * 1024), b""):
                    dst.write(chunk)

            command = [
                "ffmpeg",
                # Never wait on a terminal. Without this ffmpeg can block
                # forever asking to overwrite something.
                "-nostdin",
                "-v", "error",
                "-i", str(source),
                "-vf", SCALE,
                "-c:v", "libx264",
                "-crf", CRF,
                "-preset", "medium",
                "-pix_fmt", "yuv420p",
                # Two-second keyframes. The master's are 8.3s apart, which
                # makes a browser decode up to eight seconds of video to
                # honour a seek; at two, seeking feels immediate.
                "-g", "60",
                # Audio if there is any, dropped to something sane. The promo
                # has none today and a future upload will.
                "-c:a", "aac",
                "-b:a", "96k",
                # The index at the FRONT. Without it a browser must fetch the
                # whole file before it can play a second of it, which undoes
                # the entire point of making a smaller one.
                "-movflags", "+faststart",
                "-y",
                str(output),
            ]

            try:
                subprocess.run(
                    command,
                    check=True,
                    capture_output=True,
                    timeout=TIMEOUT_SECONDS,
                )
            except subprocess.TimeoutExpired:
                self._failed(asset, f"ffmpeg did not finish in {TIMEOUT_SECONDS}s")
                return
            except subprocess.CalledProcessError as exc:
                detail = (exc.stderr or b"").decode("utf-8", "replace").strip()
                self._failed(asset, detail[:400] or "ffmpeg exited non-zero")
                return
            except FileNotFoundError:
                # The image was built without ffmpeg. Say so plainly rather
                # than marking every video as un-previewable.
                self.stderr.write(
                    "ffmpeg is not installed in this image. See the banner in "
                    "backend/Dockerfile."
                )
                return

            if not output.exists() or output.stat().st_size == 0:
                self._failed(asset, "ffmpeg produced nothing")
                return

            # Replace rather than accumulate: rebuilding an asset should not
            # leave the previous cut orphaned on disk forever.
            if asset.preview_file:
                asset.preview_file.delete(save=False)

            with output.open("rb") as handle:
                asset.preview_file.save("preview.mp4", File(handle), save=False)

            asset.preview_state = MediaAsset.PreviewState.READY
            asset.save(
                update_fields=["preview_file", "preview_state", "updated_at"]
            )

            saved = asset.size_bytes / max(output.stat().st_size, 1)
            self.stdout.write(
                self.style.SUCCESS(
                    f"   ready — {output.stat().st_size / 1048576:.1f} MB "
                    f"({saved:.1f}x smaller)"
                )
            )

    def _failed(self, asset: MediaAsset, why: str) -> None:
        asset.preview_state = (
            MediaAsset.PreviewState.FAILED
            if asset.preview_attempts >= MAX_ATTEMPTS
            else MediaAsset.PreviewState.PENDING
        )
        asset.save(update_fields=["preview_state", "updated_at"])
        self.stderr.write(f"   failed: {why}")
