"""
Give every registered system the fifteen published security requirements.

── THE WEBSITE IS THE LIST. THIS IS A COPY OF IT. ──────────────────────────

genmars.co.ke/approach publishes these to anyone who reads the page, and calls
each tier "a gate, not a wish list". The wording below is copied from
`securityTiers` in gen-website/src/lib/company.ts, verbatim, so that a check
recorded today still says what was actually being assessed even if the page is
reworded later.

If the two ever disagree, the website is right and this is the bug. Same rule
as the tier prices in seed_services.

── IT NEVER CHANGES AN ASSESSMENT ──────────────────────────────────────────

Running this creates missing rows and leaves every existing one exactly as it
is. An assessment is somebody's judgement about a system, recorded with their
name against it; a seed command that quietly reset those would destroy the only
record of who said what, and would do it silently on the next deploy.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from portal.models import SecurityCheck, System

TIERS = {
    SecurityCheck.Tier.ONE: [
        "Secure authentication and authorisation, with least-privilege roles",
        "TLS everywhere; no plaintext transport, internal or external",
        "Automated backups with a tested restore — an untested backup is not a backup",
        "Error monitoring and alerting that reaches a human",
        "Documented development and deployment process",
        "Privacy policy and terms of service published",
    ],
    SecurityCheck.Tier.TWO: [
        "Support channel with a stated response time",
        "Personal data encrypted at rest; payment credentials never stored by us",
        "Access control reviewed and reduced to least privilege",
        "Written incident response runbook, with who is called and in what order",
        "Audit logging on sensitive actions",
    ],
    SecurityCheck.Tier.THREE: [
        "Service-level commitments with defined credits",
        "Disaster recovery with a stated recovery time and recovery point objective",
        "Scheduled security reviews and dependency audits",
        "Data-controller registration and a documented data-processing basis",
    ],
}


class Command(BaseCommand):
    help = "Create the published security requirements against every system."

    def add_arguments(self, parser):
        parser.add_argument("--slug", default=None, help="Only this system.")

    def handle(self, *args, **options):
        systems = System.objects.all()
        if options["slug"]:
            systems = systems.filter(slug=options["slug"])

        if not systems:
            self.stdout.write("No systems registered.")
            return

        created = 0
        for system in systems:
            for tier, items in TIERS.items():
                for position, item in enumerate(items, start=1):
                    _, made = SecurityCheck.objects.get_or_create(
                        system=system,
                        tier=tier,
                        position=position,
                        defaults={"item": item},
                    )
                    created += 1 if made else 0

            reached = system.security_tier_met()
            self.stdout.write(
                f"  {system.slug:22} "
                + (f"reaches {reached}" if reached else "below Tier 1")
            )

        total = sum(len(v) for v in TIERS.values())
        self.stdout.write(
            f"\n{created} requirement(s) added. "
            f"{total} apply to each of {len(systems)} system(s)."
        )
        self.stdout.write(
            "Nothing already assessed was changed — see this command's docstring."
        )
