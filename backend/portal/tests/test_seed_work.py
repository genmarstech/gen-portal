"""
What `seed_work` is allowed to put on the portfolio.

══════════════════════════════════════════════════════════════════════════════
THE COMMAND CAN CREATE A ROW THAT NAMES A CLIENT. NOTHING IN IT STOPS THAT.

`permission_on_file` records that a named company agreed in writing to be
named publicly — Charter 04 §V. A management command cannot establish that a
conversation happened, and a seeded `True` looks exactly like a real one.

The command's docstring says so, and a docstring is not a check. These tests
are the check: every item it carries must be labelled something outside
`WorkItem.NEEDS_CONSENT`, and none of them may set the flag.

`portal/migrations/0041_seed_existing_work.py` already carries the lesson —
it sets the flag False on every row it creates, because a migration is not a
signature. Neither is this.
══════════════════════════════════════════════════════════════════════════════
"""

import pytest
from django.core.management import call_command

from operations.management.commands.seed_work import ITEMS
from portal.models import WorkItem

pytestmark = pytest.mark.django_db


def test_nothing_seedable_needs_somebody_else_s_signature():
    needs = [i["slug"] for i in ITEMS if i["label"] in WorkItem.NEEDS_CONSENT]
    assert needs == [], (
        "These entries name a client, and a command cannot establish that the "
        f"client agreed to be named: {needs}"
    )


def test_no_entry_sets_the_consent_flag():
    claims = [i["slug"] for i in ITEMS if i.get("permission_on_file")]
    assert claims == [], f"A seeded permission is indistinguishable from a real one: {claims}"


def test_no_entry_claims_a_result_nobody_measured():
    """
    Charter 04 §IV. `results` invites a figure, and the one place a figure
    gets invented is a field that looks empty and wants filling.
    """
    claims = [i["slug"] for i in ITEMS if i.get("results")]
    assert claims == [], f"Remove the figure or measure it: {claims}"


def test_every_entry_survives_the_model_s_own_validation():
    """
    `full_clean` is what catches a picture with no photographer beside it —
    an Unsplash licence breach that looks like a cosmetic gap. Running the
    command is the only way to find out, so the test runs it.
    """
    call_command("seed_work", verbosity=0)
    assert WorkItem.objects.filter(slug__in=[i["slug"] for i in ITEMS]).count() == len(ITEMS)


def test_running_it_twice_changes_nothing():
    """
    It creates by slug and skips anything already there, so an item edited in
    operations cannot be undone by somebody re-running this.
    """
    call_command("seed_work", verbosity=0)
    first = WorkItem.objects.get(slug=ITEMS[0]["slug"])
    first.summary = "Edited by a human in operations."
    first.save(update_fields=["summary"])

    call_command("seed_work", verbosity=0)

    first.refresh_from_db()
    assert first.summary == "Edited by a human in operations."
    assert WorkItem.objects.filter(slug__in=[i["slug"] for i in ITEMS]).count() == len(ITEMS)


def test_everything_it_seeds_would_actually_appear():
    """
    `is_published` is the tick box; `is_publishable` is the server's answer to
    "would this appear if the site built right now". An item seeded as
    published that is not publishable is a row nobody will ever notice is
    missing.
    """
    call_command("seed_work", verbosity=0)
    for entry in ITEMS:
        item = WorkItem.objects.get(slug=entry["slug"])
        assert item.is_publishable is bool(entry.get("is_published")), item.slug
