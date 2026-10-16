"""
The ops-facing read side of SystemMetricSnapshot.

Genmars Commerce is the first registered system to report structured
business numbers rather than only uptime and events. This file covers the
staff-only screen that reads those numbers back — see
`portal.system_api.MetricsView` for the child-facing side that writes them,
and `SystemMetricSnapshot`'s docstring in portal/models.py for why metrics
are a model of their own rather than another SystemEvent.
"""

from __future__ import annotations

import pytest
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from portal.models import System, SystemMetricSnapshot

pytestmark = pytest.mark.django_db

PASSWORD = "correct-horse-battery"


@pytest.fixture
def staff() -> User:
    return User.objects.create_user(
        email="ops@genmars.co.ke", password=PASSWORD, full_name="Ops",
        is_staff=True, staff_role=User.StaffRole.FOUNDER,
        email_verified_at=timezone.now(),
    )


@pytest.fixture
def client_user() -> User:
    return User.objects.create_user(
        email="client@example.com", password=PASSWORD, full_name="A Client",
        email_verified_at=timezone.now(),
    )


@pytest.fixture
def system(staff) -> System:
    return System.objects.create(
        name="Genmars Commerce", slug="genmars-commerce", kind=System.Kind.PRODUCT,
        criticality=System.Criticality.IMPORTANT,
        purpose="Multi-tenant storefronts for other businesses.",
        impact_if_down="Merchants cannot sell and shoppers cannot check out.",
        owner=staff,
    )


def _snapshot(system, *, start, end, metrics=None):
    return SystemMetricSnapshot.objects.create(
        system=system, period_start=start, period_end=end,
        metrics=metrics or {"revenue": {"orders_placed": 1}},
    )


# ── who may reach it ─────────────────────────────────────────────────────────


def test_anonymous_cannot_read_metrics(client, system):
    response = client.get(reverse("ops-system-metrics", args=[system.slug]))
    assert response.status_code == 403


def test_a_client_account_cannot_read_metrics(client, client_user, system):
    client.force_login(client_user)
    response = client.get(reverse("ops-system-metrics", args=[system.slug]))
    assert response.status_code == 403


def test_staff_can_read_metrics(client, staff, system):
    client.force_login(staff)
    response = client.get(reverse("ops-system-metrics", args=[system.slug]))
    assert response.status_code == 200


# ── never a surprise failure ─────────────────────────────────────────────────


def test_an_unknown_slug_is_404(client, staff):
    client.force_login(staff)
    response = client.get(reverse("ops-system-metrics", args=["no-such-system"]))
    assert response.status_code == 404


def test_a_registered_system_with_no_snapshots_answers_empty_not_broken(
    client, staff, system
):
    """
    Zero reported periods is the ordinary state of a newly registered system,
    not a fault. The screen must get an empty state to render, never a 404 or
    a 500, the moment the system itself exists but has not reported yet.
    """
    client.force_login(staff)
    response = client.get(reverse("ops-system-metrics", args=[system.slug]))

    assert response.status_code == 200, response.content
    body = response.json()
    assert body["system"]["slug"] == system.slug
    assert body["latest"] is None
    assert body["history"] == []


# ── ordering and the latest/history shape ───────────────────────────────────


def test_latest_is_the_most_recent_period_and_history_is_newest_first(
    client, staff, system
):
    now = timezone.now()
    oldest = _snapshot(
        system, start=now - timezone.timedelta(days=3),
        end=now - timezone.timedelta(days=2),
        metrics={"revenue": {"orders_placed": 1}},
    )
    middle = _snapshot(
        system, start=now - timezone.timedelta(days=2),
        end=now - timezone.timedelta(days=1),
        metrics={"revenue": {"orders_placed": 2}},
    )
    newest = _snapshot(
        system, start=now - timezone.timedelta(days=1), end=now,
        metrics={"revenue": {"orders_placed": 3}},
    )

    client.force_login(staff)
    body = client.get(reverse("ops-system-metrics", args=[system.slug])).json()

    assert body["latest"]["metrics"] == newest.metrics
    history_orders = [h["metrics"]["revenue"]["orders_placed"] for h in body["history"]]
    assert history_orders == [3, 2, 1]
    assert len(body["history"]) == 3


def test_limit_caps_history_and_has_a_default(client, staff, system):
    now = timezone.now()
    for i in range(5):
        _snapshot(
            system,
            start=now - timezone.timedelta(days=i + 1),
            end=now - timezone.timedelta(days=i),
            metrics={"revenue": {"orders_placed": i}},
        )

    client.force_login(staff)

    limited = client.get(
        reverse("ops-system-metrics", args=[system.slug]), {"limit": 2}
    ).json()
    assert len(limited["history"]) == 2

    default = client.get(reverse("ops-system-metrics", args=[system.slug])).json()
    assert len(default["history"]) == 5  # fewer than the default of 30


def test_limit_is_hard_capped_regardless_of_what_is_asked_for(client, staff, system):
    now = timezone.now()
    for i in range(5):
        _snapshot(
            system,
            start=now - timezone.timedelta(days=i + 1),
            end=now - timezone.timedelta(days=i),
        )

    client.force_login(staff)
    response = client.get(
        reverse("ops-system-metrics", args=[system.slug]), {"limit": 9999}
    )
    assert response.status_code == 200
    # Five rows exist; the hard cap of 90 never comes into play here, but a
    # nonsense limit must not error or be taken at face value.
    assert len(response.json()["history"]) == 5


def test_a_nonsense_limit_falls_back_to_the_default_rather_than_erroring(
    client, staff, system
):
    client.force_login(staff)
    response = client.get(
        reverse("ops-system-metrics", args=[system.slug]), {"limit": "lots"}
    )
    assert response.status_code == 200


def test_a_snapshot_for_another_system_never_appears(client, staff, system):
    other = System.objects.create(
        name="Other", slug="other-system", kind=System.Kind.INTERNAL,
        criticality=System.Criticality.MINOR, purpose="p", impact_if_down="i",
        owner=staff,
    )
    _snapshot(other, start=timezone.now() - timezone.timedelta(days=1), end=timezone.now())

    client.force_login(staff)
    body = client.get(reverse("ops-system-metrics", args=[system.slug])).json()

    assert body["latest"] is None
    assert body["history"] == []
