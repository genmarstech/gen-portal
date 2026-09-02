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
from django.utils.text import slugify

from portal.models import Service

# Ordered as §15 of the pricing model orders them: the services layer first,
# because that is what can be sold today.
CATALOGUE = [
    {
        "name": "Implementation & configuration",
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
            slug = slugify(entry["name"])
            existing = Service.objects.filter(slug=slug).first()

            if existing and not options["force"]:
                skipped += 1
                self.stdout.write(f"  = {entry['name']} (exists, left alone)")
                continue

            if existing:
                for field, value in entry.items():
                    setattr(existing, field, value)
                existing.is_active = True
                existing.save()
                updated += 1
                self.stdout.write(self.style.WARNING(f"  ~ {entry['name']} (overwritten)"))
            else:
                Service.objects.create(slug=slug, is_active=True, **entry)
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

        self.stdout.write(
            f"\n{created} created, {updated} overwritten, {skipped} left alone."
        )
        if skipped and not options["force"]:
            self.stdout.write("Use --force to overwrite existing wording.")
