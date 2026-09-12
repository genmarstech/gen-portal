"""
The first documents on genmars.co.ke/docs.

═══════════════════════════════════════════════════════════════════════════════
IT NEVER OVERWRITES. A document that already exists is left exactly as it is.

This exists to get the section off the ground, not to own it. The moment
somebody edits one of these in operations, this command must not be able to
undo that — so it creates by slug and skips anything already there.
═══════════════════════════════════════════════════════════════════════════════

── WHY THE PORTAL DOCUMENT IS PUBLISHED AND THE PLATFORM ONE IS NOT ────────────

Everything said about the client portal below is checkable in this repository:
the sign-in path, what a client can see, how the isolation works. It is
published because it is verifiable.

The Business Platform document is a DRAFT with no progress note. What it says
is only what /services/ already says in the company's own words. Nobody outside
the team can honestly say how far that work has got, and inventing a sentence
about it is exactly what Charter 04 §IV forbids — so the note is left empty for
whoever does know.

── WHAT IS DELIBERATELY NOT CLAIMED ────────────────────────────────────────────

Payments. `portal/mpesa.py` is written and pointed at Safaricom's production
API, and no live STK push has ever been fired. "Clients pay by M-Pesa" would be
a claim about something that has never happened once, so payments do not appear
below at all. Add it when the first payment goes through, not before.
"""

from django.core.management.base import BaseCommand

from portal.models import Doc

PORTAL_BODY = """\
The client portal is where a Genmars client sees their own work: what has been
ordered, what it costs, what has been agreed and where each thing has got to.
It is the same application our own team uses to run delivery, with a different
door.

## Signing in

An account is created by us when work begins, or by the client at
[app.genmars.co.ke](https://app.genmars.co.ke). Either way the person sets
their own password — nobody at Genmars ever knows it, and an invited account
holds a password that cannot be used until the invitation is redeemed.

Passwords are hashed with Argon2. Email addresses are verified with a code that
expires, repeated wrong attempts lock an account for fifteen minutes on their
own, and a failed sign-in says the same thing whether or not the address is
registered — telling the difference is a free way to find out who our clients
are.

## What a client can see

- Orders, with the reference used on every document that follows
- Quotes and contracts, each holding the figures it was issued against rather
  than today's prices
- Invoices, and what has been paid against them
- Progress on delivery, and the gates a piece of work passes before it is done
- Support tickets and change requests, with the replies on them

## What a client cannot see

Anything belonging to anybody else. Every read is scoped to the organisations
the signed-in person belongs to, in one file rather than in each screen, so
there is a single place to audit rather than forty. A reference belonging to
another client returns "not found" rather than "not allowed", because the
second answer would confirm that the record exists.

## How it is built

Django and PostgreSQL on the API side, Next.js for the two front ends, running
in containers on our own infrastructure in Europe. Backups are encrypted before
they leave the machine, to a key the server does not hold.

The source is public. Read it rather than taking any of the above on trust.
"""

PLATFORM_BODY = """\
A reusable core platform for small and medium businesses: reports, inventory,
permissions and integrations, configured per sector rather than rebuilt for
each client.

## Why we are building it

Most of the work a growing business needs is the same work. Somewhere to keep
stock, somewhere to see the numbers, a way to decide who can do what, and a way
to connect the two or three services it already pays for. Rebuilding that from
nothing for every client is slow for us and expensive for them.

## Where it stands

In development. It is not something anybody can subscribe to today, and the
prices published on [our services page](https://genmars.co.ke/services/) are
what we intend to charge rather than what anyone is paying.

If your own timeline runs past a few months it is worth a conversation now —
early clients shape what gets built first.
"""


class Command(BaseCommand):
    help = "Create the first documentation pages, if they do not exist yet."

    def handle(self, *args, **options):
        starters = [
            {
                "slug": "client-portal",
                "title": "The client portal",
                "summary": (
                    "Where a Genmars client sees their orders, quotes, "
                    "invoices and the progress of their work."
                ),
                "category": Doc.Category.PLATFORM,
                "body": PORTAL_BODY,
                "repo_url": "https://github.com/genmarstech/gen-portal",
                "order": 10,
                "is_published": True,
                "status": Doc.Status.LIVE,
                "status_note": "",
            },
            {
                "slug": "business-platform",
                "title": "Genmars Business Platform",
                "summary": (
                    "A reusable core platform for SMEs — reports, inventory, "
                    "permissions and integrations."
                ),
                "category": Doc.Category.PLATFORM,
                "body": PLATFORM_BODY,
                "repo_url": "https://github.com/genmarstech/business-os",
                "order": 20,
                # A draft. The team has to say where this actually is before it
                # goes on the public site — see the module docstring.
                "is_published": False,
                "status": Doc.Status.BUILDING,
                "status_note": "",
            },
        ]

        created = 0
        for values in starters:
            if Doc.objects.filter(slug=values["slug"]).exists():
                self.stdout.write(f"  {values['slug']}: already exists, left alone")
                continue
            doc = Doc(**values)
            doc.full_clean()
            doc.save()
            created += 1
            state = "published" if doc.is_published else "draft"
            self.stdout.write(self.style.SUCCESS(f"  {doc.slug}: created ({state})"))

        self.stdout.write("")
        self.stdout.write(f"{created} document(s) created.")
        if created:
            self.stdout.write(
                "Nothing is on genmars.co.ke until the website is deployed again."
            )
