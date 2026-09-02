"""
The seeded catalogue has to be complete, because the order page renders it.

A service with no tiers is not a cosmetic problem: the order page shows the
three sizes and lets the client pick one, so a service missing them presents an
empty choice and the client has nothing to click. A tier with no price renders
a card with a blank where the number goes.

These run the real command against a real database rather than inspecting the
CATALOGUE and TIERS constants, because the thing that can break is the wiring
between them — a service whose slug was corrected on one side and not the other
seeds fine and produces exactly the empty screen above.
"""

from __future__ import annotations

import pytest
from django.core.management import call_command

from portal.models import Service, ServiceTier

pytestmark = pytest.mark.django_db


@pytest.fixture
def seeded():
    call_command("seed_services", verbosity=0)
    return Service.objects.filter(is_active=True)


def test_every_seeded_service_has_its_three_sizes(seeded):
    for service in seeded:
        assert service.tiers.count() == 3, (
            f"{service.slug} seeded with {service.tiers.count()} tiers. The "
            "order page shows three cards and would render an empty choice."
        )


def test_every_tier_has_a_price_and_every_service_a_unit(seeded):
    for service in seeded:
        assert service.price_unit, (
            f"{service.slug} has no price unit. A monthly service rendered as "
            "a bare number reads as the whole cost of the engagement."
        )
        for tier in service.tiers.all():
            assert tier.price_kes is not None, f"{service.slug}/{tier.slug}"
            assert tier.price_kes > 0
            assert tier.lead
            assert tier.included, "a card with no bullet points"


def test_exactly_one_tier_per_service_is_open_ended(seeded):
    """
    The top size is published as "from KES X". More than one would mean two
    floors and no ceiling; none would mean the largest engagement is being
    offered at a fixed price, which is not what the website says.
    """
    for service in seeded:
        from_tiers = [t.slug for t in service.tiers.all() if t.is_from]
        assert len(from_tiers) == 1, f"{service.slug}: {from_tiers}"


def test_running_it_twice_changes_nothing(seeded):
    before = {
        (t.service.slug, t.slug, t.price_kes)
        for t in ServiceTier.objects.select_related("service")
    }
    call_command("seed_services", verbosity=0)
    after = {
        (t.service.slug, t.slug, t.price_kes)
        for t in ServiceTier.objects.select_related("service")
    }
    assert before == after


def test_a_price_corrected_on_the_website_is_pushed_through(seeded):
    """
    Tiers are overwritten on every run, unlike the scope wording.

    That wording is contract scaffolding staff are meant to edit. A tier is a
    published price, and one in here that disagrees with genmars.co.ke is the
    exact bug — a client quoted one number and billed another — that this
    command exists to prevent.
    """
    from decimal import Decimal

    tier = ServiceTier.objects.get(service__slug="implementation", slug="essential-setup")
    original = tier.price_kes
    ServiceTier.objects.filter(pk=tier.pk).update(price_kes=Decimal("1.00"))

    call_command("seed_services", verbosity=0)

    tier.refresh_from_db()
    assert tier.price_kes == original
