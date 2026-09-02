"""
Load the runbook files into the systems registry.

The files in docs/runbooks/ are the source; `System.runbook` is a copy that
exists so the Systems screen can show it. See that directory's README for why
the source is a file: a runbook readable only inside the portal is useless
during the outage it was written for.

Every system's runbook gets `_incident-response.md` appended, because who is
called and in what order does not change per system, and a copy per file is
three copies to forget to update.
"""

from __future__ import annotations

from pathlib import Path

from django.core.management.base import BaseCommand

from portal.models import System

def _find_runbooks() -> Path:
    """
    Where the files are, in whichever layout this is running in.

    In a checkout the repository root is four levels up from this file. In the
    api container only `backend/` is copied in, and compose mounts the runbooks
    at /app/docs/runbooks — so both are tried rather than assuming one, which
    is how a command works locally and silently finds nothing in production.
    """
    here = Path(__file__).resolve()
    for candidate in (
        here.parents[3] / "docs" / "runbooks",   # container: /app/docs/runbooks
        here.parents[4] / "docs" / "runbooks",   # checkout: <repo>/docs/runbooks
    ):
        if candidate.is_dir():
            return candidate
    return here.parents[4] / "docs" / "runbooks"


RUNBOOKS = _find_runbooks()
SHARED = "_incident-response.md"


class Command(BaseCommand):
    help = "Load docs/runbooks/*.md into the systems registry."

    def handle(self, *args, **options):
        if not RUNBOOKS.is_dir():
            self.stderr.write(f"No runbook directory at {RUNBOOKS}")
            return

        shared_path = RUNBOOKS / SHARED
        shared = shared_path.read_text().strip() if shared_path.exists() else ""
        if not shared:
            self.stdout.write(
                self.style.WARNING(
                    f"  ! {SHARED} is missing — no escalation section will be "
                    "attached, which is the half people actually need."
                )
            )

        loaded = 0
        for system in System.objects.all():
            path = RUNBOOKS / f"{system.slug}.md"
            if not path.exists():
                self.stdout.write(
                    self.style.WARNING(f"  ! {system.slug}: no runbook file")
                )
                continue

            body = path.read_text().strip()
            system.runbook = (body + "\n\n\n" + shared).strip() if shared else body
            system.save(update_fields=["runbook"])
            loaded += 1
            self.stdout.write(f"  = {system.slug:16} {len(system.runbook)} chars")

        missing = System.objects.filter(runbook="").count()
        self.stdout.write(f"\n{loaded} runbook(s) loaded.")
        if missing:
            self.stdout.write(
                self.style.WARNING(
                    f"{missing} system(s) still have none. The Systems screen "
                    "says so on the card, which is the point."
                )
            )
