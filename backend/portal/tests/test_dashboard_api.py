"""
Dashboard API tests.

The isolation tests cover the selectors. These cover the HTTP surface, where the
failure modes are different: an unauthenticated request getting through, a 403
that confirms a reference exists, or a serializer quietly widening what a client
can see.
"""

from __future__ import annotations

import datetime as dt
import json

import pytest
from django.urls import reverse
from django.utils import timezone

from accounts.models import Membership, Organisation, User
from portal.models import Milestone, Order, ProgressNote

pytestmark = pytest.mark.django_db

PASSWORD = "correct-horse-battery"


@pytest.fixture(autouse=True)
def _clear_throttles():
    from django.core.cache import cache

    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def staff() -> User:
    return User.objects.create_user(
        email="edwin@genmars.co.ke",
        password=PASSWORD,
        full_name="Edwin Muchemi Wamuyu",
        is_staff=True,
    )


@pytest.fixture
def world(staff):
    org_a = Organisation.objects.create(name="Acme Ltd")
    org_b = Organisation.objects.create(name="Beta Ltd")

    user_a = User.objects.create_user(
        email="a@acme.example", password=PASSWORD, email_verified_at=timezone.now()
    )
    user_b = User.objects.create_user(
        email="b@beta.example", password=PASSWORD, email_verified_at=timezone.now()
    )
    Membership.objects.create(user=user_a, organisation=org_a)
    Membership.objects.create(user=user_b, organisation=org_b)

    order_a = Order.objects.create(
        organisation=org_a,
        reference="GM-001",
        title="M-Pesa reconciliation",
        scope="Reconcile M-Pesa against invoices automatically.",
        exclusions="Does not include migrating historical records before 2026.",
        contact=staff,
        status=Order.Status.ACTIVE,
    )
    order_b = Order.objects.create(
        organisation=org_b, reference="GM-002", title="Beta booking",
        scope="Booking system.", contact=staff,
    )
    ProgressNote.objects.create(
        order=order_a, author=staff, body="Reconciliation engine passing on real data.",
        week_of=dt.date(2026, 8, 24), published_at=timezone.now(),
    )
    ProgressNote.objects.create(
        order=order_a, author=staff, body="Draft — not sent yet.",
        week_of=dt.date(2026, 8, 31),
    )
    Milestone.objects.create(
        order=order_a, name="Deposit", amount_kes="150000.00",
        status=Milestone.Status.PAID, position=1,
    )
    Milestone.objects.create(
        order=order_a, name="On acceptance", amount_kes="150000.00", position=2
    )
    return locals()


def sign_in(client, email):
    client.post(
        reverse("sign-in"),
        {"email": email, "password": PASSWORD},
        content_type="application/json",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Authentication
# ─────────────────────────────────────────────────────────────────────────────


def test_orders_require_authentication(client, world):
    assert client.get(reverse("order-list")).status_code in (401, 403)


def test_order_detail_requires_authentication(client, world):
    assert client.get(reverse("order-detail", args=["GM-001"])).status_code in (401, 403)


def test_export_requires_authentication(client, world):
    assert client.get(reverse("export")).status_code in (401, 403)


# ─────────────────────────────────────────────────────────────────────────────
# Isolation over HTTP
# ─────────────────────────────────────────────────────────────────────────────


def test_client_sees_only_their_own_orders(client, world):
    sign_in(client, "a@acme.example")
    refs = [o["reference"] for o in client.get(reverse("order-list")).json()["orders"]]
    assert refs == ["GM-001"]


def test_another_organisations_order_is_404_not_403(client, world):
    """
    A 403 would confirm the reference exists, turning URL guessing into a client
    list. It must be indistinguishable from an order that was never created.
    """
    sign_in(client, "a@acme.example")
    real_but_theirs = client.get(reverse("order-detail", args=["GM-002"]))
    never_existed = client.get(reverse("order-detail", args=["GM-999"]))
    assert real_but_theirs.status_code == never_existed.status_code == 404
    assert real_but_theirs.json() == never_existed.json()


def test_staff_flag_alone_shows_no_client_data(client, world):
    """`is_staff` means Genmars, not "sees everything through the client API"."""
    sign_in(client, "edwin@genmars.co.ke")
    body = client.get(reverse("order-list")).json()
    assert body["orders"] == []
    assert body["has_orders"] is False


# ─────────────────────────────────────────────────────────────────────────────
# Content
# ─────────────────────────────────────────────────────────────────────────────


def test_order_detail_shows_scope_and_exclusions(client, world):
    """Charter 05 §I — exclusions are part of the promise, not fine print."""
    sign_in(client, "a@acme.example")
    body = client.get(reverse("order-detail", args=["GM-001"])).json()
    assert body["scope"]
    assert "historical records" in body["exclusions"]


def test_order_detail_names_the_contact(client, world):
    sign_in(client, "a@acme.example")
    contact = client.get(reverse("order-detail", args=["GM-001"])).json()["contact"]
    assert contact["full_name"] == "Edwin Muchemi Wamuyu"
    # Nothing beyond a name and an address. Staff account state is not exposed.
    assert set(contact) == {"full_name", "email"}


def test_unpublished_notes_are_not_returned(client, world):
    """A draft is not a promise. The client sees what they were actually told."""
    sign_in(client, "a@acme.example")
    notes = client.get(reverse("order-detail", args=["GM-001"])).json()["notes"]
    assert len(notes) == 1
    assert "Draft" not in notes[0]["body"]


def test_milestone_amounts_are_strings_not_floats(client, world):
    """Money through a float is money you cannot reconcile."""
    sign_in(client, "a@acme.example")
    milestones = client.get(reverse("order-detail", args=["GM-001"])).json()["milestones"]
    assert all(isinstance(m["amount_kes"], str) for m in milestones)


def test_empty_state_is_not_an_error(client, world):
    """Signed up, nothing agreed yet — the common early case."""
    User.objects.create_user(
        email="orphan@example.com", password=PASSWORD, email_verified_at=timezone.now()
    )
    sign_in(client, "orphan@example.com")
    r = client.get(reverse("order-list"))
    assert r.status_code == 200
    assert r.json() == {"orders": [], "has_orders": False, "has_enquiry": False}


# ─────────────────────────────────────────────────────────────────────────────
# Export — Charter 05 §VIII
# ─────────────────────────────────────────────────────────────────────────────


def test_export_returns_the_account_and_its_orders(client, world):
    sign_in(client, "a@acme.example")
    r = client.get(reverse("export"))
    assert r.status_code == 200
    payload = json.loads(r.content)
    assert payload["account"]["email"] == "a@acme.example"
    assert [o["reference"] for o in payload["orders"]] == ["GM-001"]
    assert payload["orders"][0]["progress_notes"][0]["body"]


def test_export_is_scoped_to_the_requesting_user(client, world):
    sign_in(client, "b@beta.example")
    payload = json.loads(client.get(reverse("export")).content)
    assert [o["reference"] for o in payload["orders"]] == ["GM-002"]


def test_export_downloads_as_a_file(client, world):
    """Charter 05 §VIII is about handing data back, not rendering it on a page."""
    sign_in(client, "a@acme.example")
    r = client.get(reverse("export"))
    assert "attachment" in r["Content-Disposition"]


def test_export_contains_no_password_material(client, world):
    sign_in(client, "a@acme.example")
    raw = client.get(reverse("export")).content.decode().lower()
    for leak in ("password", "argon2", "pbkdf2", "code_hash", "session"):
        assert leak not in raw, f"export leaked {leak!r}"
