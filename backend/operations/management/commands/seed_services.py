"""
Seed the service catalogue from the Technical Team Service & Pricing Model.

── WHY A COMMAND AND NOT A DATA MIGRATION ──────────────────────────────────

Catalogue wording is meant to be edited by staff — that is the whole point of
the Services screen. A data migration that created these rows would re-create
them on any rebuild of the database and would sit in the history as though the
wording were structural. It is not; it is content, and content that improves.

So: idempotent, matched on slug, and it does NOT overwrite a row somebody has
since edited unless --force is given. Running it twice is safe. Running it
after a year of edits does nothing, which is the correct behaviour.

── THE WORDING HERE IS NOT THE WEBSITE'S WORDING ───────────────────────────

genmars.co.ke/services carries marketing copy, written to be read by a
prospect. These are contract scaffolding: the scope, exclusions and
deliverables that pre-fill an order and end up snapshotted into a signed SOW.
The exclusions especially have a different job — they exist to be specific and
slightly unwelcome, because exclusions written from scratch under time pressure
come out thin, and thin exclusions are what month three argues about.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand
from decimal import Decimal

from portal.models import Service, ServiceTier

# Ordered as §15 of the pricing model orders them: the services layer first,
# because that is what can be sold today.
CATALOGUE = [
    {
        "name": "Implementation & configuration",
        "slug": "implementation",
        "summary": "One-time setup, configuration, migration and go-live.",
        "default_scope": (
            "Discovery workshop and documented configuration plan.\n"
            "Environment setup: development, staging and production, separated.\n"
            "Core configuration and workflow setup as agreed at discovery.\n"
            "User accounts and role assignment for the agreed user count.\n"
            "Data migration from the agreed source systems.\n"
            "One training session for named administrators.\n"
            "Documented go-live with an agreed rollback plan."
        ),
        "default_exclusions": (
            "Anything not named in the signed implementation scope. Additional "
            "requirements become a change request or a separate engagement.\n"
            "Historical data beyond the agreed migration window.\n"
            "Integrations with systems not listed at discovery.\n"
            "Custom report or dashboard development.\n"
            "Ongoing hosting, monitoring or support after handover — that is a "
            "managed services engagement and is quoted separately.\n"
            "Data cleansing. We migrate what is there; we do not correct it."
        ),
        "default_deliverables": (
            "Configuration plan\n"
            "Configured environments (dev, staging, production)\n"
            "Migrated data with a reconciliation summary\n"
            "Administrator training session\n"
            "Runbook and rollback plan\n"
            "Documented acceptance criteria, signed off before go-live"
        ),
    },
    {
        "name": "Integrations & custom development",
        "slug": "custom-development",
        "summary": "Paid engineering outside the standard product.",
        "default_scope": (
            "Technical specification produced and agreed before implementation.\n"
            "Implementation of the integrations or extensions named in that "
            "specification.\n"
            "Automated tests covering the agreed acceptance criteria.\n"
            "Deployment to the agreed environments.\n"
            "Handover documentation covering credentials, failure modes and "
            "monitoring."
        ),
        "default_exclusions": (
            "Feasibility or delivery dates before technical review.\n"
            "Changes to third-party systems we do not control, including their "
            "downtime, rate limits and breaking changes.\n"
            "Licences, API fees or third-party subscriptions.\n"
            "Ongoing maintenance of the integration after the agreed support "
            "period.\n"
            "Rework caused by the counterparty changing their API, which is "
            "quoted as new work."
        ),
        "default_deliverables": (
            "Technical specification\n"
            "Working integration deployed to production\n"
            "Automated test coverage of the acceptance criteria\n"
            "Monitoring and failure handling\n"
            "Handover documentation with credential ownership stated"
        ),
    },
    {
        "name": "Application managed services",
        "slug": "managed-services",
        "summary": "Recurring monitoring, backups, patching and support.",
        "default_scope": (
            "Monitoring with alerting appropriate to the agreed tier.\n"
            "Backups at the agreed frequency, with stated retention.\n"
            "A restore test at the agreed interval — a backup nobody has "
            "restored is a hope, not a backup.\n"
            "Security and dependency patching.\n"
            "User administration within the agreed allowance.\n"
            "Support during the hours stated in the agreement."
        ),
        "default_exclusions": (
            "New features or enhancements. This covers keeping what exists "
            "running.\n"
            "Infrastructure and hosting costs, which are bounded or billed "
            "separately — an unlimited resource commitment inside a fixed "
            "monthly fee is a promise that gets quietly broken.\n"
            "Incidents caused by client-side changes we were not told about.\n"
            "Support outside the agreed hours unless the tier includes it.\n"
            "Third-party vendor faults, beyond reporting and chasing them."
        ),
        "default_deliverables": (
            "Monitoring and alerting, live\n"
            "Backup schedule with stated retention and tested restores\n"
            "Patching log\n"
            "Periodic service report at the agreed interval\n"
            "Named contact and escalation path"
        ),
    },
    {
        "name": "Genmars SecureCare",
        "slug": "securecare",
        "summary": "Security and data-protection readiness, and technical support.",
        "default_scope": (
            "Security assessment at the agreed interval.\n"
            "Access review across the agreed systems.\n"
            "MFA guidance and rollout support.\n"
            "Backup review.\n"
            "Endpoint review at the agreed depth.\n"
            "Staff awareness material at the agreed interval.\n"
            "Written security report per cycle."
        ),
        "default_exclusions": (
            "Any guarantee of legal or regulatory compliance. This is readiness "
            "and technical support, and must never be described as more.\n"
            "Legal advice — that is coordinated with qualified legal "
            "professionals, not provided by us.\n"
            "Penetration testing unless separately scoped.\n"
            "24/7 incident response unless the tier includes it.\n"
            "Remediation engineering, which is quoted as custom work.\n"
            "Third-party security failures outside the reviewed systems."
        ),
        "default_deliverables": (
            "Security assessment report per cycle\n"
            "Access review findings with recommended actions\n"
            "MFA rollout guidance\n"
            "Backup and endpoint review\n"
            "Staff awareness material"
        ),
    },
    {
        "name": "Digital transformation advisory",
        "slug": "advisory",
        "summary": "Assessment, process mapping and a costed roadmap.",
        "default_scope": (
            "Business, process and technology assessment.\n"
            "Process mapping to the agreed depth.\n"
            "Technology review of current systems.\n"
            "A roadmap over the agreed horizon, with indicative budget.\n"
            "Vendor evaluation where the tier includes it.\n"
            "Presentation of findings."
        ),
        "default_exclusions": (
            "Implementation of anything the roadmap recommends. That is quoted "
            "separately, and the roadmap is deliberately useful without it.\n"
            "Procurement or contract negotiation with third parties.\n"
            "Legal, tax or regulatory advice.\n"
            "Guaranteed outcomes or savings figures.\n"
            "Ongoing advisory beyond the agreed engagement."
        ),
        "default_deliverables": (
            "Assessment report\n"
            "Process maps at the agreed depth\n"
            "Technology review\n"
            "Roadmap with indicative budget\n"
            "Findings presentation\n"
            "The client owns all of the above outright"
        ),
    },
    {
        "name": "ComplianceReady",
        "slug": "complianceready",
        "summary": "Data mapping, gap analysis, policy templates and controls review.",
        "default_scope": (
            "Initial assessment against the agreed framework.\n"
            "Data mapping: what personal data is held, where, and why.\n"
            "Gap analysis with prioritised findings.\n"
            "Policy templates for the agreed areas.\n"
            "Controls review.\n"
            "Implementation roadmap.\n"
            "Staff awareness material where the tier includes it."
        ),
        "default_exclusions": (
            "Legal advice, and any warranty that the client is compliant. This "
            "is readiness and support work; formal advice is coordinated with "
            "qualified legal professionals.\n"
            "Representation before any regulator.\n"
            "Implementation of the recommended controls, quoted separately.\n"
            "Ongoing monitoring after the engagement.\n"
            "Certification or audit, which we do not perform."
        ),
        "default_deliverables": (
            "Assessment report\n"
            "Data map\n"
            "Gap analysis with prioritised findings\n"
            "Policy templates\n"
            "Controls review\n"
            "Implementation roadmap"
        ),
    },
    {
        "name": "Product training",
        "slug": "training",
        "summary": "Enablement for users, administrators and partners.",
        "default_scope": (
            "Sessions at the agreed count and duration.\n"
            "Training material for the agreed audience.\n"
            "Administrator training where the tier includes it.\n"
            "Session recording where the tier includes it.\n"
            "Certification of attendees where the tier includes it."
        ),
        "default_exclusions": (
            "Attendees beyond the agreed count.\n"
            "Custom curriculum development unless the tier includes it.\n"
            "Travel and venue costs for on-site delivery.\n"
            "Ongoing support, which is a managed services engagement.\n"
            "Any guarantee of user adoption. We can teach the software; we "
            "cannot make people use it."
        ),
        "default_deliverables": (
            "Delivered sessions\n"
            "Training material\n"
            "Recording where included\n"
            "Attendance and certification record where included"
        ),
    },
]


# ── TIERS ────────────────────────────────────────────────────────────────────
#
# THE WEBSITE IS THE PRICE LIST. This is a copy of it, so a signed-in client
# can pick a size inside the portal instead of being sent out to genmars.co.ke
# to read a number and come back.
#
# It is generated from `offers` in gen-website/src/lib/company.ts, and only
# from the entries marked `available: "now"` — the platform tiers are published
# as coming, and something we cannot deliver yet must not be orderable.
#
# WHEN A PRICE CHANGES ON THE WEBSITE, RE-RUN `seed_services --force`.
# Two price lists is how a client is quoted one number and billed another, and
# nothing here notices on its own if the website moves. Prices are stored as
# numbers rather than the website's display strings because picking a tier has
# to answer the budget question on the order form, which needs arithmetic.

# Charged how. From `unit` on the same website entries the tiers come from.
UNITS = {
    "implementation": "one-time",
    "custom-development": "starting",
    "managed-services": "per month",
    "securecare": "per month",
    "advisory": "starting",
    "complianceready": "starting",
    "training": "per session"
}

TIERS = {
    "implementation": [
        {
            "slug": "essential-setup",
            "name": "Essential Setup",
            "price_kes": "25000",
            "is_from": False,
            "lead": "A straightforward single-location setup.",
            "includes": "Discovery\nBasic configuration\nUp to 10 users\nBasic data migration\n1 integration\n1 training session\n3 days go-live support",
            "position": 1,
        },
        {
            "slug": "business-setup",
            "name": "Business Setup",
            "price_kes": "75000",
            "is_from": False,
            "lead": "Real data to migrate and workflows to configure.",
            "includes": "Discovery\nAdvanced configuration\nUp to 30 users\nFull data migration\nUp to 2 integrations\n2 training sessions\nFull documentation\n7 days go-live support",
            "position": 2,
        },
        {
            "slug": "enterprise-setup",
            "name": "Enterprise Setup",
            "price_kes": "150000",
            "is_from": True,
            "lead": "Multiple locations and systems.",
            "includes": "Discovery\nCustom configuration\n30+ users\nAdvanced data migration\nMultiple integrations\nCustom training\nFull documentation\n14+ days go-live support",
            "position": 3,
        },
    ],
    "custom-development": [
        {
            "slug": "basic",
            "name": "Basic",
            "price_kes": "50000",
            "is_from": False,
            "lead": "One low-complexity integration or extension.",
            "includes": "Technical specification\nOne integration or extension\nAutomated tests\nDeployment\nHandover documentation",
            "position": 1,
        },
        {
            "slug": "advanced",
            "name": "Advanced",
            "price_kes": "150000",
            "is_from": False,
            "lead": "Multiple integrations, custom interfaces and reporting.",
            "includes": "Technical specification\nMultiple integrations\nCustom UI, API or reporting\nAutomated tests\nDeployment\nHandover documentation",
            "position": 2,
        },
        {
            "slug": "enterprise",
            "name": "Enterprise",
            "price_kes": "350000",
            "is_from": True,
            "lead": "Large integrations, complex workflows, portals and apps.",
            "includes": "Separate discovery phase\nComplex workflows\nPortals or mobile apps\nHigh-assurance testing\nDeployment and monitoring\nFull handover documentation",
            "position": 3,
        },
    ],
    "managed-services": [
        {
            "slug": "care",
            "name": "Care",
            "price_kes": "10000",
            "is_from": False,
            "lead": "Keeping a running system running.",
            "includes": "Monitoring\nDaily backups\nUpdates and patches\nLimited user management\nBusiness-hours support\nStandard SLA",
            "position": 1,
        },
        {
            "slug": "business-care",
            "name": "Business Care",
            "price_kes": "25000",
            "is_from": False,
            "lead": "Extended support and someone whose name you know.",
            "includes": "Monitoring\nDaily backups\nUpdates and patches\nFull user management\nExtended support hours\nAdvanced performance work\nMonthly report\nDedicated contact\nPriority SLA",
            "position": 2,
        },
        {
            "slug": "enterprise-care",
            "name": "Enterprise Care",
            "price_kes": "60000",
            "is_from": True,
            "lead": "Round-the-clock cover on a custom SLA.",
            "includes": "Monitoring\nAdvanced backups\nUpdates and patches\nFull user management\n24/7 support option\nAdvanced performance work\nMonthly report\nDedicated contact\nCustom SLA",
            "position": 3,
        },
    ],
    "securecare": [
        {
            "slug": "basic",
            "name": "Basic",
            "price_kes": "15000",
            "is_from": False,
            "lead": "A quarterly look, and the basics enforced.",
            "includes": "Quarterly security assessment\nAccess review\nMFA guidance\nBackup review\nBasic endpoint review\nQuarterly awareness material\nQuarterly report",
            "position": 1,
        },
        {
            "slug": "business",
            "name": "Business",
            "price_kes": "35000",
            "is_from": False,
            "lead": "Monthly cadence, with incident support.",
            "includes": "Monthly security assessment\nAccess review\nMFA guidance\nBackup review\nFull endpoint review\nMonthly awareness material\nIncident support\nMonthly report",
            "position": 2,
        },
        {
            "slug": "plus",
            "name": "Plus",
            "price_kes": "75000",
            "is_from": True,
            "lead": "Continuous, with reporting an executive can read.",
            "includes": "Continuous assessment\nAccess review\nMFA guidance\nBackup review\nAdvanced endpoint review\nCustom awareness programme\nPriority incident support\nExecutive and monthly reporting",
            "position": 3,
        },
    ],
    "advisory": [
        {
            "slug": "assessment",
            "name": "Assessment",
            "price_kes": "35000",
            "is_from": False,
            "lead": "Where you actually are, and a first roadmap.",
            "includes": "Business, process and technology assessment\nBasic roadmap\nFindings presentation",
            "position": 1,
        },
        {
            "slug": "transformation-plan",
            "name": "Transformation Plan",
            "price_kes": "100000",
            "is_from": False,
            "lead": "A twelve-month plan with a budget attached.",
            "includes": "Process mapping\nTechnology review\n12-month roadmap\nIndicative budget\nFindings presentation",
            "position": 2,
        },
        {
            "slug": "strategic-advisory",
            "name": "Strategic Advisory",
            "price_kes": "250000",
            "is_from": True,
            "lead": "Two years out, with vendor evaluation and workshops.",
            "includes": "12-24 month roadmap\nVendor evaluation\nWorkshops\nOngoing advisory\nFindings presentation",
            "position": 3,
        },
    ],
    "complianceready": [
        {
            "slug": "compliance-check",
            "name": "Compliance Check",
            "price_kes": "35000",
            "is_from": False,
            "lead": "Where the gaps are.",
            "includes": "Initial assessment\nGap analysis\nBasic controls review",
            "position": 1,
        },
        {
            "slug": "compliance-ready",
            "name": "Compliance Ready",
            "price_kes": "85000",
            "is_from": False,
            "lead": "Know what data you hold, and have the policies written.",
            "includes": "Data mapping\nGap analysis\nPolicy templates\nControls review\nImplementation roadmap",
            "position": 2,
        },
        {
            "slug": "compliance-program",
            "name": "Compliance Program",
            "price_kes": "200000",
            "is_from": True,
            "lead": "The whole programme, with help putting it in place.",
            "includes": "Comprehensive programme\nData mapping\nPolicy templates\nStaff awareness\nImplementation support\nControls review",
            "position": 3,
        },
    ],
    "training": [
        {
            "slug": "essential",
            "name": "Essential",
            "price_kes": "15000",
            "is_from": False,
            "lead": "One session, for a small group.",
            "includes": "1 session\nUp to 2 hours\nUp to 10 participants\nTraining materials",
            "position": 1,
        },
        {
            "slug": "professional",
            "name": "Professional",
            "price_kes": "35000",
            "is_from": False,
            "lead": "Three sessions, recorded, with admin training.",
            "includes": "3 sessions\nUp to 6 hours\nUp to 25 participants\nTraining materials\nRecorded sessions\nAdmin training\nCertification",
            "position": 2,
        },
        {
            "slug": "enterprise",
            "name": "Enterprise",
            "price_kes": "75000",
            "is_from": True,
            "lead": "Built around your own configuration.",
            "includes": "Custom session count\nCustom duration\n25+ participants\nTraining materials\nRecorded sessions\nAdmin training\nCertification\nCustom curriculum",
            "position": 3,
        },
    ],
}


class Command(BaseCommand):
    help = "Seed the service catalogue from the Service & Pricing Model (§15)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="Overwrite services that already exist, discarding local edits.",
        )
        parser.add_argument(
            "--retire",
            metavar="SLUG",
            nargs="*",
            default=[],
            help=(
                "Deactivate these slugs. Retiring, never deleting: an order may "
                "already reference the service, and a deleted row would break it."
            ),
        )

    def handle(self, *args, **options):
        created = updated = skipped = 0

        for entry in CATALOGUE:
            # Matched on NAME, not slug. The slug is now authored to match
            # genmars.co.ke rather than derived from the name, so an existing
            # row seeded under the old slugify() value must be FOUND and
            # corrected rather than duplicated alongside a new one.
            existing = Service.objects.filter(name__iexact=entry["name"]).first()

            if existing and not options["force"]:
                # One exception to leaving edited rows alone: the SLUG. Wording
                # is content somebody may have improved; the slug is the join
                # between the website and this database, and a stale one
                # silently drops the service off every order placed from the
                # site. Correct it and say so.
                if existing.slug != entry["slug"]:
                    was = existing.slug
                    existing.slug = entry["slug"]
                    existing.save(update_fields=["slug"])
                    self.stdout.write(
                        self.style.WARNING(f"  > {entry['name']}: slug {was} -> {entry['slug']}")
                    )
                skipped += 1
                self.stdout.write(f"  = {entry['name']} (wording left alone)")
                continue

            if existing:
                for field, value in entry.items():
                    setattr(existing, field, value)
                existing.is_active = True
                existing.save()
                updated += 1
                self.stdout.write(self.style.WARNING(f"  ~ {entry['name']} (overwritten)"))
            else:
                Service.objects.create(is_active=True, **entry)
                created += 1
                self.stdout.write(self.style.SUCCESS(f"  + {entry['name']}"))

        for slug in options["retire"]:
            service = Service.objects.filter(slug=slug).first()
            if service is None:
                self.stdout.write(self.style.ERROR(f"  ? {slug} not found"))
            elif not service.is_active:
                self.stdout.write(f"  = {slug} already retired")
            else:
                service.is_active = False
                service.save(update_fields=["is_active"])
                self.stdout.write(self.style.WARNING(f"  - {slug} retired"))

        self._seed_tiers(force=options["force"])

        self.stdout.write(
            f"\n{created} created, {updated} overwritten, {skipped} left alone."
        )
        if skipped and not options["force"]:
            self.stdout.write("Use --force to overwrite existing wording.")

    def _seed_tiers(self, *, force: bool) -> None:
        """
        The three sizes each service is sold in.

        Matched on (service, slug), which is what identifies a tier — the slugs
        are not globally unique, because "enterprise" is a tier of several
        services and "basic" of two.

        Unlike the scope wording above, tiers ARE overwritten by default. That
        wording is contract scaffolding staff are meant to edit; a tier is a
        published price, and a price in here that disagrees with the website is
        the bug this command exists to prevent. The only thing --force changes
        is whether an unrecognised extra tier is removed, because deleting a
        row somebody added deliberately should take a deliberate flag.
        """
        self.stdout.write("\nTiers")
        written = 0

        for service_slug, tiers in TIERS.items():
            service = Service.objects.filter(slug=service_slug).first()
            if service is None:
                self.stdout.write(
                    self.style.ERROR(f"  ? {service_slug} not in the catalogue")
                )
                continue

            unit = UNITS.get(service_slug, "")
            if unit and service.price_unit != unit:
                service.price_unit = unit
                service.save(update_fields=["price_unit"])

            for entry in tiers:
                fields = dict(entry)
                price = fields.pop("price_kes")
                ServiceTier.objects.update_or_create(
                    service=service,
                    slug=fields.pop("slug"),
                    defaults={
                        **fields,
                        "price_kes": Decimal(price) if price else None,
                    },
                )
                written += 1

            known = {t["slug"] for t in tiers}
            extra = service.tiers.exclude(slug__in=known)
            if extra.exists():
                names = ", ".join(extra.values_list("slug", flat=True))
                if force:
                    extra.delete()
                    self.stdout.write(
                        self.style.WARNING(f"  - {service_slug}: removed {names}")
                    )
                else:
                    self.stdout.write(
                        self.style.WARNING(
                            f"  ! {service_slug} has {names}, which the website "
                            "does not publish. --force removes them."
                        )
                    )

            self.stdout.write(f"  = {service_slug}: {len(tiers)} tiers")

        self.stdout.write(f"{written} tier(s) written.")

