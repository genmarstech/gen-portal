"""
Carry the three existing pieces of work out of gen-website and into the database.

── WHY A DATA MIGRATION RATHER THAN "RE-ENTER THEM" ────────────────────────────

They already existed, as a hand-edited array in
gen-website/src/lib/company.ts, complete with summaries and capability lists
somebody had written carefully. Deleting that array without bringing the
content across would have thrown away real work and quietly reset the two
clients' consent state to "nobody remembers".

── BOTH CLIENTS ARRIVE UNPUBLISHED, WITH NO PERMISSION ON FILE ─────────────────

Exactly the state they were in: `permissionOnFile: false` in the array, so
invisible on the site. They stay invisible here — the public queryset refuses a
client label without a signature regardless — but they are now somewhere an
editor can find them and tick the box the day the permission arrives.

⚠ DO NOT SET permission_on_file HERE, EVER. Charter 04 §V wants written
  permission that can be produced if challenged, and a migration cannot have
  obtained one.
"""

from django.db import migrations


def carry_across(apps, schema_editor):
    WorkItem = apps.get_model("portal", "WorkItem")

    for values in [
        {
            "slug": "business-platform",
            "name": "Genmars Business Platform",
            "category": "software",
            "label": "product",
            "sector": "Retail & multi-branch operations",
            "year": "2026",
            "url": "https://business.genmars.co.ke",
            "summary": (
                "A multi-tenant point of sale and back office: one deployment "
                "serving many independent businesses, each seeing only its own "
                "branches, stock and takings."
            ),
            "detail": (
                "A till that scans, prices and takes payment; branches and "
                "registers with a shift and a drawer to reconcile; a catalogue "
                "where a shelf price carries its own VAT rule; stock that only "
                "moves with a reason attached; and sales, refunds and reports on "
                "the other side of the counter. Cashiers sign in at the register "
                "with credentials that belong to the business employing them and "
                "never reach Genmars."
            ),
            "capabilities": (
                "Multi-tenant isolation\nPoint of sale\nVAT-inclusive pricing\n"
                "Stock with an audit trail"
            ),
            "architecture": (
                "Django and DRF behind one Caddy host that splits by path, with a "
                "Next.js application for the back office and a browser-held "
                "session for the till. Tenant isolation lives in a single module "
                "rather than in each view, and a read that falls outside a "
                "caller's scope answers 404 rather than 403 — a 403 confirms the "
                "row exists, which is an enumeration oracle in a different "
                "costume."
            ),
            "engineering": (
                "Two tiers of identity that never cross: subscribers authenticate "
                "through a Genmars account, while a shop's own cashiers hold "
                "tenant-local credentials that cannot authenticate anywhere else. "
                "Checkout carries an idempotency key, so a retried sale returns "
                "the original rather than charging twice. Transaction history is "
                "immutable — a cancellation marks the sale cancelled and a refund "
                "is written beside it, so what was charged stays what was charged."
            ),
            # Ours. Nobody to ask, so it can go straight out.
            "permission_on_file": False,
            "is_published": True,
            "order": 10,
        },
        {
            "slug": "avinterra",
            "name": "Avinterra Expeditions",
            "category": "sites",
            "label": "client",
            "sector": "Travel & tourism",
            "year": "2026",
            "url": "https://avinterra.tours",
            "summary": (
                "A booking and enquiry site for a Kenyan travel house running "
                "guided safaris, coastal trips and international departures."
            ),
            "detail": (
                "Trip packages with duration, location and dual-currency pricing; "
                "an interactive departure map across eight-plus regions; "
                "instalment payment options; and M-Pesa paybill details alongside "
                "WhatsApp enquiry routing, which is how this market actually books."
            ),
            "capabilities": (
                "Multi-currency pricing\nInteractive map\nM-Pesa paybill\n"
                "WhatsApp enquiry routing"
            ),
            "permission_on_file": False,
            "is_published": False,
            "order": 20,
        },
        {
            "slug": "clips-serenity-spa",
            "name": "Clips Serenity Spa",
            "category": "sites",
            "label": "client",
            "sector": "Health & wellness",
            "year": "2026",
            "url": "https://clipsserenityspa.co.ke",
            "summary": (
                "An online booking system for a Nairobi hair, beauty and wellness "
                "spa open seven days a week."
            ),
            "detail": (
                "Appointment reservation with therapist selection and "
                "confirmation, a published service menu with transparent pricing, "
                "staff profiles, embedded maps for a physical location, and "
                "M-Pesa, card and cash payment paths. Walk-ins still work; the "
                "booking flow exists to guarantee a slot."
            ),
            "capabilities": (
                "Appointment booking\nTherapist selection\nM-Pesa & card payments\n"
                "Maps integration"
            ),
            "permission_on_file": False,
            "is_published": False,
            "order": 30,
        },
    ]:
        WorkItem.objects.update_or_create(slug=values["slug"], defaults=values)


def take_them_back(apps, schema_editor):
    WorkItem = apps.get_model("portal", "WorkItem")
    WorkItem.objects.filter(
        slug__in=["business-platform", "avinterra", "clips-serenity-spa"]
    ).delete()


class Migration(migrations.Migration):
    dependencies = [("portal", "0040_workitem")]
    operations = [migrations.RunPython(carry_across, take_them_back)]
