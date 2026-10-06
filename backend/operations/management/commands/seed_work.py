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

── THE ADDRESSES ARE THE SUFFIXED ONES, AND THAT IS NOT A TYPO ────────────────

`njia.vercel.app` and the rest are taken globally, so Vercel assigned
`njia-eta`, `shamba-kappa`, `ratiba-nine`, `kioo-blue` and `mizani-hazel`.
The older projects in the team got unsuffixed names before that; these did
not.

⚠ EACH ONE WAS FETCHED BEFORE IT WAS WRITTEN HERE. A new Vercel project is
  created with Vercel Authentication on, and a protected deployment answers
  200 with a login page — so a status code is not evidence that a link
  works. What was checked is that following the redirects lands back on the
  deployment's own host rather than on vercel.com/login.

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
    {
        "slug": "njia-arrival-board",
        "name": "Njia",
        "category": WorkItem.Category.APP,
        "label": WorkItem.Label.RESEARCH,
        "sector": "Transport",
        "year": "2026",
        "url": "https://njia-eta.vercel.app",
        "summary": (
            "An arrival board for six Nairobi matatu corridors, with a map "
            "drawn from twenty-five coordinates and no tile server."
        ),
        "detail": (
            "Pick a stop and the board lists what is coming, how far away it "
            "is, and how much to trust the number. Beside it, a map shows "
            "every vehicle on the six corridors; underneath, a panel names "
            "the pairs that have closed up on each other.\n\n"
            "The routes and the places are real. Every vehicle, time and "
            "delay is generated in the browser from a fixed seed, and the "
            "page says so above the fold in body type rather than in a "
            "footnote — a board that looks like this and is not connected to "
            "anything is the one dishonest thing the project could do.\n\n"
            "Bunching is the number it exists to show. The vehicle in front "
            "picks up everybody waiting, dwells longer and falls behind; the "
            "one behind arrives at emptied stops and catches it. From the "
            "kerb that is a twenty-minute wait and then three at once, on a "
            "route that genuinely runs every six minutes. It emerges from "
            "dispatch jitter and a spread of driving paces with no "
            "special-casing: two bunches across the city at 06:30, seven by "
            "08:00."
        ),
        "capabilities": (
            "Canvas cartography without a map library\n"
            "Deterministic simulation\n"
            "Headway and bunching analysis\n"
            "Accessible data tables\n"
            "Light and dark themes\n"
            "No dependencies beyond React"
        ),
        "architecture": (
            "Vite, React and TypeScript, deployed as a static build. The map "
            "is a canvas and nine lines of arithmetic: equirectangular about "
            "the centre of the data, with longitude scaled by the cosine of "
            "the latitude. Across Nairobi that is wrong by a few metres, "
            "against coordinates approximate to a block — three orders of "
            "magnitude under the data's own precision.\n\n"
            "A slippy map would have put a third party's CDN in the critical "
            "path of the page's centrepiece, needed a key in a public "
            "bundle, and shipped two hundred kilobytes to draw twenty-five "
            "dots and six lines."
        ),
        "engineering": (
            "A vehicle's position is integrated from its departure every time "
            "it is asked for, rather than advanced a tick at a time and "
            "stored. That costs a few thousand multiplications a second and "
            "buys the property that makes the thing arguable: scrubbing the "
            "clock back to 07:41 shows exactly what 07:41 showed, whether you "
            "arrived by playing forwards, by dragging backwards, or by "
            "reloading the page. The clock is the only accumulating state in "
            "the application.\n\n"
            "The board is the product and the map is the illustration. The "
            "table carries every fact the drawing does, is first in the "
            "source, and moves above the canvas below 880px — because a "
            "twenty-five-dot diagram at the size of a stamp is not what "
            "somebody standing at a stage needs. Every estimate prints a "
            "range: a single number for a vehicle twenty minutes out claims a "
            "precision the model does not have, and a board that prints one "
            "teaches people to distrust all of its numbers."
        ),
        "is_published": True,
        "order": 10,
    },
    {
        "slug": "shamba-field-records",
        "name": "Shamba",
        "category": WorkItem.Category.APP,
        "label": WorkItem.Label.RESEARCH,
        "sector": "Agriculture",
        "year": "2026",
        "url": "https://shamba-kappa.vercel.app",
        "summary": (
            "An offline-first field survey that converges after a week with "
            "no signal, and shows what the usual merge rule quietly discards."
        ),
        "detail": (
            "An extension officer works six smallholdings in a valley with no "
            "coverage. So does a colleague. Some of the farms are the same "
            "farms, and on Sunday evening both phones reach the server at "
            "once.\n\n"
            "The first half is a working record editor: edits persist on the "
            "device, the outbox survives a reload, and the network condition "
            "is a control rather than something to wait for. The second runs "
            "the same seven days under two merge rules side by side.\n\n"
            "Both converge. That is why the wrong one survives in production "
            "— it passes the test everybody writes. It is also eating "
            "observations, and the page lists the six it discarded by Sunday, "
            "each by a named officer. An argument about merge semantics is "
            "not winnable in the abstract."
        ),
        "capabilities": (
            "Conflict-free replicated data types\n"
            "Field-level merge with Lamport clocks\n"
            "Durable outbox and retry\n"
            "Simulated network conditions\n"
            "Light and dark themes\n"
            "No dependencies beyond React"
        ),
        "architecture": (
            "Vite, React and TypeScript, with persistence behind a load/save "
            "pair so nothing above it knows which store is underneath. The "
            "server is a second replica in the same tab and the transport is "
            "simulated, so its failure modes can be chosen rather than waited "
            "for.\n\n"
            "The merge is field-level, because two officers editing different "
            "fields of one record must both keep their edit — and that is the "
            "common case, not the exotic one."
        ),
        "engineering": (
            "The tiebreak is a Lamport counter, not a wall clock. A field "
            "phone's clock is set by the handset, and a handset that has been "
            "off the network for a week can be wrong by days: one device "
            "running three days fast would win every conflict it was ever "
            "part of, for ever, and the data would look fine.\n\n"
            "Tallies are not registers. Two officers each logging four "
            "sightings on one plot leaves four under last-write-wins — "
            "correctly, under the wrong rule for that field. Counts are held "
            "per device and summed. Deletion is a tombstone, because a record "
            "dropped from the map comes back on the next merge carrying "
            "whatever the replica that had not heard last knew.\n\n"
            "The outbox has no sequence numbers, no deduplication table and "
            "no exactly-once delivery. All of that machinery exists to stop a "
            "message being applied twice, and the merge is idempotent, so "
            "there is nothing left for it to protect. Choosing a data model "
            "that cannot be corrupted by a retry is cheaper than building the "
            "apparatus that prevents retries — and the case that proves it, "
            "an acknowledgement lost after the server has already applied the "
            "push, is one of the network conditions on the page."
        ),
        "is_published": True,
        "order": 11,
    },
    {
        "slug": "ratiba-roster-solver",
        "name": "Ratiba",
        "category": WorkItem.Category.TOOL,
        "label": WorkItem.Label.RESEARCH,
        "sector": "Workforce",
        "year": "2026",
        "url": "https://ratiba-nine.vercel.app",
        "summary": (
            "A duty roster solved by constraint search, which names the shift "
            "and the rule when no roster exists."
        ),
        "detail": (
            "Forty-two shifts over seven days at a 24-hour pharmacy, filled "
            "from ten people with contracts, qualifications, leave and a legal "
            "right to rest. Four weeks to choose from; one of them has no "
            "answer.\n\n"
            "The eleven-hour daily rest period is what makes it hard. A night "
            "runs 21:00 to 07:00, so the next morning starts with zero hours "
            "of rest and the next evening with seven: one night shift removes "
            "two of the following day's three options for that person, and "
            "the effect chains down the week. A rota built row by row — which "
            "is how a spreadsheet builds one — paints itself into a corner on "
            "Friday, and the person holding the pen unpicks Monday by hand.\n\n"
            "The rules are switches on the page rather than fallbacks in the "
            "code. A solver that quietly relaxed the rest period to produce an "
            "answer would be making an employment-law decision on behalf of "
            "whoever read its output."
        ),
        "capabilities": (
            "Constraint satisfaction search\n"
            "Explainable infeasibility\n"
            "Fairness optimisation\n"
            "Cancellable background computation\n"
            "Light and dark themes\n"
            "No dependencies beyond React"
        ),
        "architecture": (
            "Vite, React and TypeScript. Backtracking with dynamic "
            "minimum-remaining-values ordering: at every node the next shift "
            "filled is whichever currently has the fewest legal people. That "
            "is the whole reason it finishes — filling the week in calendar "
            "order means discovering on Friday that Monday was wrong.\n\n"
            "Then hill-climbing over swaps for the soft constraints. Deciding "
            "whether a rota is legal and deciding whether it is kind want "
            "different algorithms, and every state the climb visits is legal "
            "by construction, so there is no repair step.\n\n"
            "Every week that has a rota finds one in 42 to 51 nodes. The week "
            "that has none is proved impossible in 96."
        ),
        "engineering": (
            "The valuable output is the one where it fails. Two counting "
            "arguments run before the search, and they exist for the message "
            "rather than the speed: the search finds the same impossibility in "
            "milliseconds and cannot phrase it. \u201cFriday needs three "
            "pharmacists across the day and only two are available\u201d is an "
            "instruction; \u201cno solution found\u201d is not. The third "
            "mechanism is the search's own record of where it kept failing, "
            "reported from the deepest point reached, naming the rule that "
            "removed each person.\n\n"
            "The search runs in a Web Worker in chunks, yielding between them. "
            "A worker inside a recursive search is not reading its message "
            "queue, so a cancel posted to it arrives when the search finishes "
            "— the one moment cancelling is worthless.\n\n"
            "Solved, disproved and abandoned are three different outcomes and "
            "get three different words. When the node cap is reached the page "
            "says it gave up and says in so many words that this is not a "
            "proof. Collapsing that into \u201cimpossible\u201d is how a tool "
            "starts telling managers that legal rotas are illegal."
        ),
        "is_published": True,
        "order": 12,
    },
    {
        "slug": "kioo-design-system",
        "name": "Kioo",
        "category": WorkItem.Category.DESIGN_SYSTEM,
        "label": WorkItem.Label.INTERNAL,
        "sector": "Design",
        "year": "2026",
        "url": "https://kioo-blue.vercel.app",
        "summary": (
            "The company's own design system, whose accessibility figures are "
            "measured in the browser rather than written down."
        ),
        "detail": (
            "Four brand constants, a set of semantic tokens, eight components "
            "and one rule the whole thing exists to enforce: a brand constant "
            "is never a background for semantic text, because one theme is "
            "then readable and the other is not — and whoever built it was in "
            "the theme where it worked.\n\n"
            "The components are deliberately plain. A design system earns its "
            "keep on the field that wires its own label, the tab strip that is "
            "one tab stop rather than five, the dialog that gives focus back, "
            "and the money type that refuses a fractional cent."
        ),
        "capabilities": (
            "Design tokens for two themes\n"
            "Runtime contrast auditing\n"
            "WCAG 2.2 AA throughout\n"
            "Keyboard-complete components\n"
            "Accessible forms and dialogs\n"
            "No dependencies beyond React"
        ),
        "architecture": (
            "Vite, React and TypeScript. One file is the design system — the "
            "token layer — and everything else consumes it: nothing outside it "
            "names a colour.\n\n"
            "Every dark value is assigned twice, once inside the "
            "prefers-color-scheme media query for the un-stamped default and "
            "once under a data-theme attribute for an explicit choice, because "
            "a theme has three states and the third is the one most people are "
            "in. The colours themselves are still declared once and both "
            "places reference them. That second assignment is also what lets "
            "the audit render both palettes in one document and measure them."
        ),
        "engineering": (
            "Thirty contrast pairs are measured when the page loads, from "
            "probe elements given a token as their colour inside a scope "
            "carrying each theme, read back as resolved values. A design "
            "system that documents its ratios in a file documents the ratios "
            "it had on the day somebody typed them; the colours move, the file "
            "does not, and the file becomes a claim about accessibility that "
            "is quietly false.\n\n"
            "That paid for itself during the build. The audit came back red on "
            "a pair nobody had thought to check — the border of every input in "
            "the system, at 1.79:1 against the 3:1 that WCAG 2.2 asks of a "
            "control boundary. A perfectly pleasant hairline, and below the "
            "line. It was not found by reading the file. All thirty pairs now "
            "pass in both themes.\n\n"
            "The same discipline is already enforced in continuous integration "
            "across the company's three front ends; this is where the rule is "
            "written down and demonstrated."
        ),
        "is_published": True,
        "order": 13,
    },
    {
        "slug": "mizani-query-engine",
        "name": "Mizani",
        "category": WorkItem.Category.TOOL,
        "label": WorkItem.Label.RESEARCH,
        "sector": "Data",
        "year": "2026",
        "url": "https://mizani-hazel.vercel.app",
        "summary": (
            "A columnar store and a query engine that run in the browser tab, "
            "with a plan that reports what the query actually read."
        ),
        "detail": (
            "Sixty thousand till lines, a dictionary-encoded column store, a "
            "hand-written subset of SQL, and a bar chart drawn on a canvas. "
            "Drop a CSV of your own on the page and it is read in the same tab "
            "— nothing is uploaded, and there is no server to upload it to.\n\n"
            "The sample is generated in the browser from a seed and is not "
            "anybody's takings. It is built as CSV text and handed to the same "
            "reader a dropped file goes through, because a sample dataset that "
            "takes a different code path to the user's own is a sample dataset "
            "that can be green while the real thing is broken."
        ),
        "capabilities": (
            "Columnar storage with dictionary encoding\n"
            "SQL tokeniser, parser and planner\n"
            "Query plans with real measurements\n"
            "RFC 4180 CSV parsing\n"
            "Canvas charting\n"
            "No dependencies beyond React"
        ),
        "architecture": (
            "Vite, React and TypeScript. Text columns are a dictionary and an "
            "array of integer codes; numeric columns are typed arrays. A query "
            "reads the two columns it needs and leaves the other eight alone, "
            "and the plan reports which and how many bytes.\n\n"
            "The table lives in a Web Worker and is never posted back. "
            "postMessage copies, so sending it to the page would serialise "
            "megabytes on every load and then run every query on the thread "
            "trying to paint. A statement goes in; a few hundred rows come "
            "out."
        ),
        "engineering": (
            "The dictionary is what makes it fast rather than merely small. A "
            "text equality resolves the literal to one integer before the scan "
            "and then compares integers — and if the literal is not in the "
            "dictionary at all the predicate is false for every row, so the "
            "scan is skipped and the plan says so. A spelling mistake in a "
            "filter returns in two tenths of a millisecond instead of reading "
            "a column.\n\n"
            "Expressions are compiled to a closure once rather than "
            "interpreted per row, so the switch on node kind happens at "
            "compile time instead of a quarter of a million times during a "
            "scan.\n\n"
            "Every parse error carries a character offset, from the tokeniser "
            "through the parser and out of the worker into a caret under the "
            "word. A parser that reports \u201csyntax error\u201d has not "
            "finished the job. That is also why the engine parses one "
            "statement shape rather than borrowing a full SQL parser: a full "
            "one would accept a join and then fail at execution with a message "
            "about an internal node type."
        ),
        "is_published": True,
        "order": 14,
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
