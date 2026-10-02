"""
Genmars' own work on genmars.co.ke/work.

═══════════════════════════════════════════════════════════════════════════════
IT NEVER OVERWRITES, like seed_docs. An item that already exists is left
exactly as it is.

This exists to get a piece onto the portfolio without somebody retyping six
paragraphs into a form, not to own the wording afterwards. The moment anybody
edits one of these in operations, this command must not be able to undo it —
so it creates by slug and skips anything already there.
═══════════════════════════════════════════════════════════════════════════════

── WHY ONLY OUR OWN WORK IS SEEDABLE ───────────────────────────────────────────

Everything here carries a label outside WorkItem.NEEDS_CONSENT, and that is a
rule rather than a coincidence. A client item needs `permission_on_file`, which
records that a named company agreed in writing to be named publicly — Charter
04 §V. A management command cannot establish that a conversation happened, and
a seeded `True` would look exactly like a real one.

`portal/migrations/0041_seed_existing_work.py` already carries the lesson: it
sets permission_on_file = False on every row it creates, because a migration is
not a signature. Neither is this.

── `results` IS LEFT EMPTY ON PURPOSE ──────────────────────────────────────────

The field invites a number, and Charter 04 §IV forbids one that was not
measured. Nothing below has visitors, conversion or uptime worth reporting, so
the field stays blank and the engineering section does the convincing instead.
That is what the model's own help text asks for.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from portal.models import WorkItem

ITEMS = [
    {
        "slug": "stk-lifecycle",
        "name": "Waiting for the Customer",
        "category": WorkItem.Category.TOOL,
        "label": WorkItem.Label.RESEARCH,
        "sector": "Payments",
        "year": "2026",
        "url": "https://stk-lifecycle.vercel.app",
        "summary": (
            "An interactive teardown of the M-Pesa STK push lifecycle, and why "
            "a till must ask Safaricom rather than believe the callback."
        ),
        "detail": (
            "Two simulated tills consume one stream of events. One believes the "
            "callback Safaricom posts to it; the other asks Safaricom on a "
            "schedule and treats a callback only as a hint that it is worth "
            "asking early. Five scenarios play out on a shared clock — the "
            "customer pays, the customer ignores the prompt, the callback is "
            "lost in transit, somebody forges a callback, and the money arrives "
            "after the cashier has already given up.\n\n"
            "Three of the five end with the two tills disagreeing about whether "
            "a sale was paid for, and the page shows the exact second each "
            "disagreement opens. One of the three is a theft: the callback URL "
            "has to be public for Safaricom to reach it, so anybody can reach "
            "it, and nothing in the body proves who sent it.\n\n"
            "The timeline can be played, paused and scrubbed, and the wire log "
            "beneath it shows the request and callback shapes a developer "
            "actually meets, with the result codes that account for almost "
            "every push."
        ),
        "capabilities": (
            "Interactive simulation\n"
            "Payments domain modelling\n"
            "Developer documentation\n"
            "Light and dark themes\n"
            "No dependencies beyond React"
        ),
        "architecture": (
            "Vite, React and TypeScript, deployed as a static build. One module "
            "holds the whole model: scenarios are lists of timed events, and a "
            "single pure function returns both tills' beliefs at any instant.\n\n"
            "That purity is load-bearing rather than tidy. State is recomputed "
            "from the event list instead of accumulated, so scrubbing the "
            "timeline backwards gives the same answer as playing forwards to "
            "the same point. A simulation whose history depends on how you "
            "arrived cannot be used to argue about anything, and this one "
            "exists to make an argument."
        ),
        "engineering": (
            "It simulates and never calls anything. No request leaves the "
            "browser, there is no network code in the project and no credential "
            "in the repository — which is the only honest way to publish a "
            "walkthrough of a payment API.\n\n"
            "The claim it makes is one Genmars implements in production: a "
            "callback never decides anything. It records that something "
            "happened and prompts an immediate query, and only Safaricom's own "
            "answer moves a payment to paid. A forged callback therefore costs "
            "one outbound query and achieves nothing, and the till keeps "
            "working on the days the callback never arrives at all.\n\n"
            "Not affiliated with or endorsed by Safaricom, and deliberately not "
            "dressed in their colours: it carries Genmars' own palette and "
            "typefaces, and the single green in it is the one that means paid. "
            "The page says so in its own words, above the fold."
        ),
        "is_published": True,
        "order": 0,
    },
]


class Command(BaseCommand):
    help = "Create Genmars' own /work items. Never overwrites an existing one."

    def handle(self, *args, **options):
        created = skipped = 0

        for entry in ITEMS:
            existing = WorkItem.objects.filter(slug=entry["slug"]).first()
            if existing is not None:
                skipped += 1
                self.stdout.write(f"  = {entry['slug']} (left exactly as it is)")
                continue

            item = WorkItem(**entry)
            # The model's own rule about pictures and their credits, run here
            # too rather than trusted — this writes the same rows the ops form
            # writes, so it answers to the same validation.
            item.full_clean()
            item.save()
            created += 1
            self.stdout.write(self.style.SUCCESS(f"  + {entry['slug']}"))

            # `is_published` is the tick box; `is_publishable` is the server's
            # answer to "would this appear if the site built right now". They
            # differ for an item needing consent, and saying so here is the
            # difference between a founder believing it is live and it being
            # live.
            if not item.is_publishable:
                self.stdout.write(
                    self.style.WARNING(
                        f"    {item.slug} is published but NOT publishable — "
                        "it will not appear on the site."
                    )
                )

        self.stdout.write(f"\n{created} created, {skipped} left alone.")
        if created:
            self.stdout.write(
                "Saving is not publishing: the next gen-website build is what "
                "puts this on the internet."
            )
