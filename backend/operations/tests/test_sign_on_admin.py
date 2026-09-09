"""
Configuring which sibling applications may sign our people in.

The permission split is the point of this file. Reading is staff, because an
engineer wiring up a sibling needs the client_id and the registered addresses
and none of it is secret. Every WRITE is founder-only, because deciding which
application may authenticate our people is deciding who has access to what.
"""

from __future__ import annotations

import pytest
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from portal.models import ActivityLog, SignOnApp, System

pytestmark = pytest.mark.django_db

PASSWORD = "correct-horse-battery"
CALLBACK = "https://business-os.genmars.co.ke/auth/callback"


def _staff(email: str, role: str) -> User:
    return User.objects.create_user(
        email=email,
        password=PASSWORD,
        is_staff=True,
        staff_role=role,
        email_verified_at=timezone.now(),
    )


@pytest.fixture
def founder() -> User:
    return _staff("founder@genmars.co.ke", User.StaffRole.FOUNDER)


@pytest.fixture
def engineer() -> User:
    return _staff("delivery@genmars.co.ke", User.StaffRole.DELIVERY)


@pytest.fixture
def system(founder) -> System:
    return System.objects.create(
        name="Business OS",
        slug="business-os",
        kind=System.Kind.INTERNAL,
        criticality=System.Criticality.IMPORTANT,
        purpose="Runs the company's own operations.",
        impact_if_down="Internal work stops.",
        owner=founder,
    )


def _register(client, system):
    return client.post(
        reverse("ops-sign-on"), {"system": system.slug}, content_type="application/json"
    )


def _configure(client, app, **values):
    return client.patch(
        reverse("ops-sign-on-detail", args=[app.pk]),
        values,
        content_type="application/json",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Who may do what
# ─────────────────────────────────────────────────────────────────────────────


def test_any_staff_account_can_read_the_engineering_list(client, engineer, system):
    client.force_login(engineer)
    body = client.get(reverse("ops-sign-on")).json()

    assert body["may_edit"] is False
    row = next(r for r in body["systems"] if r["system"]["slug"] == "business-os")
    assert row["registered"] is False


def test_a_founder_is_told_they_may_edit(client, founder, system):
    client.force_login(founder)
    assert client.get(reverse("ops-sign-on")).json()["may_edit"] is True


def test_an_engineer_cannot_register_an_application(client, engineer, system):
    client.force_login(engineer)
    assert _register(client, system).status_code == 403
    assert SignOnApp.objects.count() == 0


def test_an_engineer_cannot_change_one(client, founder, engineer, system):
    client.force_login(founder)
    _register(client, system)
    app = SignOnApp.objects.get()

    client.force_login(engineer)
    assert _configure(client, app, redirect_uris=[CALLBACK]).status_code == 403
    assert _configure(client, app, is_enabled=True).status_code == 403
    assert client.post(reverse("ops-sign-on-secret", args=[app.pk])).status_code == 403

    app.refresh_from_db()
    assert app.redirect_uris == []
    assert app.is_enabled is False
    assert app.has_secret is False


# ─────────────────────────────────────────────────────────────────────────────
# Registering, in the order it has to happen
# ─────────────────────────────────────────────────────────────────────────────


def test_a_new_registration_is_inert(client, founder, system):
    client.force_login(founder)
    body = _register(client, system).json()

    assert body["sign_on"]["client_id"].startswith("gsso_")
    assert body["sign_on"]["is_enabled"] is False
    assert body["sign_on"]["has_secret"] is False
    assert body["sign_on"]["is_usable"] is False
    assert set(body["sign_on"]["why_not_usable"]) == {
        "no redirect address registered",
        "no secret issued",
        "turned off",
    }


def test_a_system_can_only_be_registered_once(client, founder, system):
    client.force_login(founder)
    assert _register(client, system).status_code == 201
    assert _register(client, system).status_code == 400


def test_it_cannot_be_turned_on_before_it_would_work(client, founder, system):
    client.force_login(founder)
    _register(client, system)
    app = SignOnApp.objects.get()

    refused = _configure(client, app, is_enabled=True)
    assert refused.status_code == 400
    assert "no redirect address registered" in refused.json()["detail"]
    assert "no secret issued" in refused.json()["detail"]

    app.refresh_from_db()
    assert app.is_enabled is False


def test_the_whole_setup_in_the_order_it_happens(client, founder, system):
    client.force_login(founder)
    _register(client, system)
    app = SignOnApp.objects.get()

    assert _configure(client, app, redirect_uris=[CALLBACK]).status_code == 200

    minted = client.post(reverse("ops-sign-on-secret", args=[app.pk]))
    assert minted.status_code == 201
    secret = minted.json()["secret"]
    assert secret.startswith("gsec_")
    assert "cannot be shown again" in minted.json()["secret_notice"]

    turned_on = _configure(client, app, is_enabled=True)
    assert turned_on.status_code == 200
    assert turned_on.json()["sign_on"]["is_usable"] is True
    assert turned_on.json()["sign_on"]["why_not_usable"] == []


# ─────────────────────────────────────────────────────────────────────────────
# The https rule, at the surface a person actually types into
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "bad",
    ["http://business-os.genmars.co.ke/cb", "https://localhost:3000/cb", "https://127.0.0.1/cb"],
)
def test_a_dev_address_is_refused_with_the_address_in_the_message(
    client, founder, system, bad
):
    client.force_login(founder)
    _register(client, system)
    app = SignOnApp.objects.get()

    refused = _configure(client, app, redirect_uris=[bad])
    assert refused.status_code == 400
    assert refused.json()["field"] == "redirect_uris"
    # Names the offending address. "One of these is wrong" is a message people
    # answer by deleting all of them.
    assert bad in refused.json()["detail"]

    app.refresh_from_db()
    assert app.redirect_uris == []


def test_the_same_address_twice_is_refused(client, founder, system):
    client.force_login(founder)
    _register(client, system)
    app = SignOnApp.objects.get()
    assert _configure(client, app, redirect_uris=[CALLBACK, CALLBACK]).status_code == 400


# ─────────────────────────────────────────────────────────────────────────────
# The log
# ─────────────────────────────────────────────────────────────────────────────


def test_configuration_is_logged_and_the_secret_never_is(client, founder, system):
    client.force_login(founder)
    _register(client, system)
    app = SignOnApp.objects.get()
    _configure(client, app, redirect_uris=[CALLBACK])
    secret = client.post(reverse("ops-sign-on-secret", args=[app.pk])).json()["secret"]
    _configure(client, app, is_enabled=True)
    _configure(client, app, is_enabled=False)

    actions = list(ActivityLog.objects.order_by("id").values_list("action", flat=True))
    assert actions == [
        ActivityLog.Action.SIGN_ON_CONFIGURED,     # registered
        ActivityLog.Action.SIGN_ON_CONFIGURED,     # redirect address
        ActivityLog.Action.SIGN_ON_SECRET_ISSUED,
        ActivityLog.Action.SIGN_ON_CONFIGURED,     # turned on
        ActivityLog.Action.SIGN_ON_DISABLED,
    ]

    # The log is a file on disk read by everyone with operations access, and it
    # ends up in a backup. Nothing derived from the secret may be in it.
    app.refresh_from_db()
    assert app.secret_prefix  # there is one, and it must not appear below
    written = str(list(ActivityLog.objects.values_list("summary", "detail")))
    assert secret not in written
    assert app.secret_prefix not in written
