"""
Poll every registered system's health endpoint.

Run from the same timer as the uptime check. Two things are deliberately true
about it:

  · It never raises. A command that dies on the first unreachable system stops
    checking the rest, which is precisely backwards — the ones after it are the
    ones you now know nothing about.

  · It only ever GETs a health URL, with a short timeout and no credentials. It
    is not a way to reach into a system; see System's docstring on why the
    parent watches rather than reaches.
"""

from __future__ import annotations

import urllib.error
import urllib.request

from django.core.management.base import BaseCommand
from django.utils import timezone

from portal.models import System

TIMEOUT = 8
USER_AGENT = "genmars-parent (+https://genmars.co.ke)"


class Command(BaseCommand):
    help = "Check the health endpoint of every registered system."

    def add_arguments(self, parser):
        parser.add_argument(
            "--slug", help="Check only this system.", default=None
        )

    def handle(self, *args, **options):
        systems = System.objects.exclude(health_url="").filter(
            status__in=[System.Status.LIVE, System.Status.PAUSED]
        )
        if options["slug"]:
            systems = systems.filter(slug=options["slug"])

        if not systems:
            self.stdout.write("Nothing registered with a health URL.")
            return

        down = []
        for system in systems:
            health, detail = self._probe(system.health_url)

            system.health = health
            system.health_detail = detail[:300]
            system.checked_at = timezone.now()
            system.save(update_fields=["health", "health_detail", "checked_at"])

            mark = {
                System.Health.UP: "ok  ",
                System.Health.DEGRADED: "warn",
                System.Health.DOWN: "DOWN",
            }.get(health, "?   ")
            line = f"  {mark}  {system.slug:24} {detail}"

            if health == System.Health.DOWN:
                down.append(system)
                self.stdout.write(self.style.ERROR(line))
            elif health == System.Health.DEGRADED:
                self.stdout.write(self.style.WARNING(line))
            else:
                self.stdout.write(line)

        # Non-zero so the systemd unit fails and OnFailure sends the alert. The
        # exit code is the whole integration; there is no mail sent from here.
        if down:
            critical = [s for s in down if s.criticality == System.Criticality.CRITICAL]
            self.stderr.write(
                f"\n{len(down)} system(s) down"
                + (f", {len(critical)} of them critical" if critical else "")
            )
            raise SystemExit(1)

    @staticmethod
    def _probe(url: str) -> tuple[str, str]:
        """
        GET it and interpret the answer.

        A 500 and a refused connection are different problems, so they get
        different states: something answering badly is DEGRADED, something not
        answering at all is DOWN. Collapsing them loses the distinction exactly
        when it matters.
        """
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                code = response.status
        except urllib.error.HTTPError as exc:
            return System.Health.DEGRADED, f"answered HTTP {exc.code}"
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            # `exc` describes the transport. No response body is read or
            # logged: a health endpoint that started returning something
            # unexpected should not end up quoted in our journal.
            return System.Health.DOWN, f"unreachable: {type(exc).__name__}"

        if 200 <= code < 300:
            return System.Health.UP, f"HTTP {code}"
        return System.Health.DEGRADED, f"HTTP {code}"
