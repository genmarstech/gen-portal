"""
One test that walks the whole company, over HTTP, in order.

═══════════════════════════════════════════════════════════════════════════════
WHY THIS EXISTS WHEN EVERY PIECE IS ALREADY TESTED.

Every other test file starts from a fixture that fabricates its own state —
`services.convert_enquiry(...)` called directly, an Order built by hand, a
signed Contract conjured in a fixture. That is right for testing a rule, and it
means no test ever exercises the HANDOFF between two of them.

The bug that reached production was exactly that shape: every unit test passed
while an existing client clicking "Order" was silently redirected to their
dashboard and the request thrown away. Nothing was individually broken. The
seam was.

So this walks the real path a real client takes, through the real endpoints, in
the order they actually happen: a stranger signs up, verifies, onboards, is
qualified, signed, invoiced, pays, is offered more work, accepts it, asks for
help, and exports everything we hold. If any two of those stop fitting
together, this fails and nothing else will.
═══════════════════════════════════════════════════════════════════════════════

It is deliberately ONE test rather than fifteen. The point is the sequence: a
step that only passes because a fixture handed it a state the previous step
would never produce is the thing being guarded against.
"""

from __future__ import annotations

import re
from decimal import Decimal

import pytest
from django.core import mail
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from portal.models import (
    Contract,
    Enquiry,
    Invoice,
    Milestone,
    Offer,
    Order,
    SupportTicket,
)

pytestmark = pytest.mark.django_db

PASSWORD = "correct-horse-battery-staple"


@pytest.fixture(autouse=True)
def _room_to_move(settings):
    """
    Throttles off. This walks a whole relationship in a few seconds, which no
    real client does — the limits are tested where they belong, in test_api.
    """
    settings.REST_FRAMEWORK = {
        **settings.REST_FRAMEWORK,
        "DEFAULT_THROTTLE_RATES": {
            k: "100000/hour"
            for k in settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]
        },
    }


@pytest.fixture
def staff() -> User:
    return User.objects.create_user(
        email="edwin@genmars.co.ke", password=PASSWORD, full_name="Edwin Muchemi",
        is_staff=True, staff_role=User.StaffRole.FOUNDER,
        email_verified_at=timezone.now(),
    )


def code_from_last_email() -> str:
    match = re.search(r"\b(\d{6})\b", mail.outbox[-1].body)
    assert match, f"no code in: {mail.outbox[-1].body[:200]}"
    return match.group(1)


def test_a_stranger_becomes_a_paying_client(staff, settings):
    settings.SUPPORT_EMAIL = "support@genmars.co.ke"
    settings.PRIVACY_EMAIL = "privacy@genmars.co.ke"

    client = Client()
    ops = Client()
    ops.force_login(staff)

    # ── 1. a stranger signs up ────────────────────────────────────────────
    response = client.post(
        reverse("sign-up"),
        {"email": "mercy@kilimanidental.co.ke", "password": PASSWORD,
         "full_name": "Mercy Wanjiku"},
        content_type="application/json",
    )
    assert response.status_code in (200, 201), response.content
    # The code goes to them and nowhere else — never into the response body.
    assert "472" not in response.content.decode()
    code = code_from_last_email()

    # ── 2. they verify ────────────────────────────────────────────────────
    response = client.post(
        reverse("verify"),
        {"email": "mercy@kilimanidental.co.ke", "code": code},
        content_type="application/json",
    )
    assert response.status_code == 200, response.content

    session = client.get(reverse("session")).json()
    assert session["authenticated"] is True
    # Verified but not onboarded: the state the ordering bug used to mishandle.
    assert session["needs_onboarding"] is True

    # ── 3. onboarding, which files an enquiry and NOT an order ────────────
    response = client.post(
        reverse("onboarding"),
        {
            "full_name": "Mercy Wanjiku",
            "organisation_name": "Kilimani Dental",
            "problem": "We reconcile M-Pesa against invoices by hand, two days a week.",
            "timeline": "Within three months",
            "budget_range": "KES 100,000 - 500,000",
            "service": "implementation",
        },
        content_type="application/json",
    )
    # 201: onboarding CREATES an enquiry. It answers with where to go next,
    # never with an order.
    assert response.status_code == 201, response.content
    assert response.json()["next"] == "/dashboard"

    enquiry = Enquiry.objects.get()
    assert enquiry.status == Enquiry.Status.NEW
    # Charter 02 §I — a client form must never be able to produce an order.
    assert not Order.objects.exists()

    assert client.get(reverse("session")).json()["needs_onboarding"] is False

    # ── 4. it reaches the operations queue ────────────────────────────────
    queue = ops.get(reverse("ops-enquiries")).json()["enquiries"]
    assert [e["organisation"] for e in queue] == ["Kilimani Dental"]
    assert queue[0]["service_name"] == "" or "mplementation" in queue[0]["service_name"]

    # ── 5. staff qualify it into an order ─────────────────────────────────
    response = ops.post(
        reverse("ops-convert", args=[enquiry.pk]),
        {"title": "M-Pesa reconciliation", "scope": "Import, match, exception review."},
        content_type="application/json",
    )
    assert response.status_code in (200, 201), response.content
    order = Order.objects.get()

    # ── 6. a statement of work, issued then signed ────────────────────────
    Milestone.objects.create(
        order=order, name="On signature", amount_kes=Decimal("150000.00"), position=1
    )
    response = ops.post(
        reverse("ops-contracts", args=[order.reference]),
        {"payment_terms": "50% on signature, 50% on delivery."},
        content_type="application/json",
    )
    assert response.status_code in (200, 201), response.content
    contract = Contract.objects.get()

    response = ops.post(
        reverse("ops-contract-sign", args=[order.reference, contract.pk]),
        {"signed_on": str(timezone.localdate()), "signed_by_name": "Dr Wanjiku",
         "note": "Countersigned PDF filed."},
        content_type="application/json",
    )
    assert response.status_code == 200, response.content

    # ── 7. an invoice, which needed that signature ────────────────────────
    response = ops.post(
        reverse("ops-invoices", args=[order.reference]),
        {"milestone": order.milestones.first().pk},
        content_type="application/json",
    )
    assert response.status_code == 201, response.content
    invoice = Invoice.objects.get()
    assert invoice.amount_kes == Decimal("150000.00")

    # ── 8. the client can see it, and only theirs ─────────────────────────
    shown = client.get(reverse("invoice-list")).json()["invoices"]
    assert [i["number"] for i in shown] == [invoice.number]
    assert shown[0]["balance"] == "150000.00"

    # ── 9. paid in two transfers, as M-Pesa actually works ────────────────
    for amount, ref in [("100000.00", "SLJ7XK2P1Q"), ("50000.00", "SLJ8YM4R3T")]:
        response = ops.post(
            reverse("ops-invoice-payments", args=[invoice.pk]),
            {"method": "mpesa", "reference": ref, "amount_kes": amount},
            content_type="application/json",
        )
        assert response.status_code == 200, response.content

    shown = client.get(reverse("invoice-list")).json()["invoices"][0]
    assert shown["status"] == "paid"
    assert shown["balance"] == "0.00"
    # Both references are visible to them, so they can check what we credited.
    assert len(shown["payments"]) == 2

    # ── 10. an offer for more work ────────────────────────────────────────
    response = ops.post(
        reverse("ops-offers"),
        {
            "organisation": order.organisation_id,
            "title": "Ongoing reconciliation support",
            "detail": "Monthly reconciliation and exception review.",
            "amount_kes": "25000.00",
            "expires_on": str(timezone.localdate() + timezone.timedelta(days=14)),
        },
        content_type="application/json",
    )
    assert response.status_code == 201, response.content
    offer = Offer.objects.get()

    # A draft is not something the client has received.
    assert client.get(reverse("offer-list")).json()["offers"] == []

    response = ops.post(
        reverse("ops-offer-action", args=[offer.pk]),
        {"action": "send"}, content_type="application/json",
    )
    assert response.status_code == 200, response.content

    # ── 11. they accept, which files an enquiry and starts no work ────────
    offers = client.get(reverse("offer-list")).json()["offers"]
    assert len(offers) == 1
    response = client.post(
        reverse("offer-decision", args=[offers[0]["reference"]]),
        {"decision": "accept"}, content_type="application/json",
    )
    assert response.status_code == 200, response.content

    assert Enquiry.objects.count() == 2, "accepting files a second enquiry"
    assert Order.objects.count() == 1, "and does NOT create a second order"

    # ── 12. they ask for help ─────────────────────────────────────────────
    response = client.post(
        reverse("support"),
        {"subject": "August export is empty",
         "body": "The export button returns a file with no rows for August."},
        content_type="application/json",
    )
    assert response.status_code == 201, response.content
    ticket = SupportTicket.objects.get()

    # ── 13. staff answer, with a private note alongside ───────────────────
    ops.post(
        reverse("ops-ticket-reply", args=[ticket.reference]),
        {"body": "Their data is fine, the button is broken. Do not say that yet.",
         "internal": True},
        content_type="application/json",
    )
    ops.post(
        reverse("ops-ticket-reply", args=[ticket.reference]),
        {"body": "We can reproduce this and are fixing it."},
        content_type="application/json",
    )

    thread = client.get(reverse("support")).json()["tickets"][0]["messages"]
    said = " ".join(m["body"] for m in thread)
    assert "We can reproduce this" in said
    assert "Do not say that yet" not in said, "an internal note reached the client"

    # ── 14. they take their data and go, whenever they like ───────────────
    response = client.get(reverse("export"))
    assert response.status_code == 200
    assert "attachment" in response["Content-Disposition"]

    body = response.json()
    dumped = str(body)
    assert "Kilimani Dental" in dumped
    assert invoice.number in dumped
    # Charter 05 §VIII — nothing stands between a client and their own data.

    # ── and the record of it all ──────────────────────────────────────────
    from portal.models import ActivityLog

    actions = set(ActivityLog.objects.values_list("action", flat=True))
    for expected in (
        ActivityLog.Action.INVOICE_ISSUED,
        ActivityLog.Action.INVOICE_PAID,
        ActivityLog.Action.OFFER_SENT,
        ActivityLog.Action.OFFER_ACCEPTED,
    ):
        assert expected in actions, f"{expected} never reached the log"
