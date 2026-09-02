"""
M-Pesa STK push.

The callback is UNAUTHENTICATED — Daraja sends no signature and no credential —
so most of what follows is about what a forged or replayed callback cannot do.
The single most important assertion in this file is that a callback claiming a
smaller amount than we asked for does not settle the invoice.

Nothing here touches Safaricom. Every HTTP call is stubbed: the credentials in
production point at api.safaricom.co.ke, where a push prompts a real phone for
real money.
"""

from __future__ import annotations

import json
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.urls import reverse
from django.utils import timezone

from accounts.models import Membership, Organisation, User
from operations import services
from portal import mpesa
from portal.models import Enquiry, Invoice, Milestone, MpesaPayment

pytestmark = pytest.mark.django_db

PASSWORD = "correct-horse-battery"


@pytest.fixture(autouse=True)
def _clear_throttles():
    from django.core.cache import cache
    cache.clear()
    yield
    cache.clear()


@pytest.fixture(autouse=True)
def _configured(settings):
    """Credentials shaped like the real ones, pointed at sandbox."""
    settings.MPESA_BASE_URL = "https://sandbox.safaricom.co.ke"
    settings.MPESA_CONSUMER_KEY = "key"
    settings.MPESA_CONSUMER_SECRET = "secret"
    settings.MPESA_CONSUMER_PASSKEY = "passkey"
    settings.MPESA_SHORT_CODE = "3437835"
    settings.MPESA_TILL_NUMBER = "6134975"
    settings.MPESA_TRANSACTION_TYPE = "CustomerBuyGoodsOnline"
    settings.MPESA_CALLBACK_URL = "https://api.genmars.co.ke/api/mpesa/callback"
    settings.MPESA_CALLBACK_TOKEN = ""
    settings.MPESA_ENABLED = True
    settings.MPESA_IS_LIVE = False


@pytest.fixture
def staff() -> User:
    return User.objects.create_user(
        email="ops@genmars.co.ke", password=PASSWORD, is_staff=True,
        staff_role=User.StaffRole.COMMERCIAL, email_verified_at=timezone.now(),
    )


@pytest.fixture
def client_user() -> User:
    return User.objects.create_user(
        email="client@example.com", password=PASSWORD, full_name="Dr Wanjiku",
        email_verified_at=timezone.now(),
    )


@pytest.fixture
def invoice(staff, client_user) -> Invoice:
    org = Organisation.objects.create(name="Kilimani Dental")
    Membership.objects.create(user=client_user, organisation=org)
    enquiry = Enquiry.objects.create(
        organisation=org, submitted_by=client_user, problem="Reconciling by hand."
    )
    order = services.convert_enquiry(
        enquiry=enquiry, actor=staff, title="Work", scope="Do the thing."
    )
    Milestone.objects.create(
        order=order, name="On signature", amount_kes=Decimal("150000.00")
    )
    contract = services.issue_contract(order=order, actor=staff)
    services.record_signature(
        contract=contract, actor=staff, signed_on=timezone.localdate(),
        signed_by_name="Dr Wanjiku", note="Filed.",
    )
    return services.issue_invoice(
        order=order, actor=staff, milestone=order.milestones.first()
    )


def push_response(checkout="ws_CO_123", merchant="29115-34620561-1"):
    return {
        "MerchantRequestID": merchant,
        "CheckoutRequestID": checkout,
        "ResponseCode": "0",
        "ResponseDescription": "Success. Request accepted for processing",
        "CustomerMessage": "Success. Request accepted for processing",
    }


def callback_body(checkout="ws_CO_123", code=0, amount=150000, receipt="SLJ7XK2P1Q"):
    """Daraja's real shape. Metadata is ABSENT on failure, which is the whole
    reason parse_callback tolerates it missing."""
    stk = {
        "MerchantRequestID": "29115-34620561-1",
        "CheckoutRequestID": checkout,
        "ResultCode": code,
        "ResultDesc": "The service request is processed successfully."
        if code == 0
        else "Request cancelled by user",
    }
    if code == 0:
        stk["CallbackMetadata"] = {
            "Item": [
                {"Name": "Amount", "Value": amount},
                {"Name": "MpesaReceiptNumber", "Value": receipt},
                {"Name": "PhoneNumber", "Value": 254712345678},
            ]
        }
    return {"Body": {"stkCallback": stk}}


# ── phone numbers people actually type ───────────────────────────────────────


@pytest.mark.parametrize(
    "typed",
    ["0712345678", "+254712345678", "254712345678", "712345678",
     "0712 345 678", "0712-345-678", "+254 712 345 678"],
)
def test_every_way_somebody_writes_their_number(typed):
    assert mpesa.normalise_phone(typed) == "254712345678"


def test_a_safaricom_1xx_number_is_accepted():
    assert mpesa.normalise_phone("0110123456") == "254110123456"


@pytest.mark.parametrize("bad", ["", "12345", "0812345678", "not a phone", "07123456789012"])
def test_nonsense_is_refused_before_safaricom_sees_it(bad):
    with pytest.raises(mpesa.MpesaError):
        mpesa.normalise_phone(bad)


# ── the password, which is where Buy Goods setups go wrong ───────────────────


def test_the_password_uses_the_short_code_not_the_till(settings):
    """
    THE CLASSIC BUY GOODS BUG.

    base64(BusinessShortCode + Passkey + Timestamp). On a Buy Goods setup the
    short code and the till are different numbers, and using the till here
    produces an invalid-credential error that reads as though the passkey is
    wrong — hours spent debugging the wrong field.
    """
    import base64

    encoded = mpesa._password("20260902120000")
    decoded = base64.b64decode(encoded).decode()

    assert decoded.startswith(settings.MPESA_SHORT_CODE)
    assert settings.MPESA_TILL_NUMBER not in decoded


def test_the_push_sends_the_till_as_partyb(invoice):
    """And the other half: PartyB is the till customers actually pay."""
    with patch("portal.mpesa.access_token", return_value="tok"), \
         patch("portal.mpesa._request", return_value=push_response()) as request:
        services.start_mpesa_payment(invoice=invoice, phone="0712345678")

    body = json.loads(request.call_args.kwargs["data"].decode())
    assert body["BusinessShortCode"] == "3437835"
    assert body["PartyB"] == "6134975"
    assert body["TransactionType"] == "CustomerBuyGoodsOnline"
    assert body["Amount"] == 150000
    assert body["PhoneNumber"] == "254712345678"
    assert body["AccountReference"] == invoice.number[:12]


# ── starting a payment ───────────────────────────────────────────────────────


def test_a_successful_push_does_not_mark_anything_paid(invoice):
    """
    A push means Safaricom accepted the prompt for delivery. The customer has
    not seen it, let alone entered a PIN. Treating the two as the same is how a
    system marks an invoice settled that nobody paid.
    """
    with patch("portal.mpesa.access_token", return_value="tok"), \
         patch("portal.mpesa._request", return_value=push_response()):
        payment = services.start_mpesa_payment(invoice=invoice, phone="0712345678")

    assert payment.status == MpesaPayment.Status.PENDING
    invoice.refresh_from_db()
    assert invoice.status == Invoice.Status.ISSUED
    assert invoice.payment_reference == ""


def test_an_invoice_with_cents_is_refused_rather_than_rounded(invoice):
    """
    Daraja's Amount is an integer. Rounding down under-collects and rounding up
    takes money the client never agreed to, so neither is done silently.
    """
    invoice.amount_kes = Decimal("133333.33")
    invoice.save(update_fields=["amount_kes"])

    with pytest.raises(services.OperationsError) as caught:
        services.start_mpesa_payment(invoice=invoice, phone="0712345678")
    assert "whole shillings" in str(caught.value)
    assert MpesaPayment.objects.count() == 0


def test_a_paid_invoice_cannot_be_prompted_again(invoice, staff):
    services.record_payment(invoice=invoice, actor=staff, reference="BANK-1")
    with pytest.raises(services.OperationsError):
        services.start_mpesa_payment(invoice=invoice, phone="0712345678")


def test_a_voided_invoice_cannot_be_prompted(invoice, staff):
    services.void_invoice(invoice=invoice, actor=staff, reason="Sent in error.")
    with pytest.raises(services.OperationsError):
        services.start_mpesa_payment(invoice=invoice, phone="0712345678")


def test_pressing_the_button_twice_supersedes_the_first_prompt(invoice):
    """Somebody whose prompt has not arrived taps again. A second prompt is
    right; two live pending rows for one invoice is not."""
    with patch("portal.mpesa.access_token", return_value="tok"), \
         patch("portal.mpesa._request", side_effect=[push_response("A"), push_response("B")]):
        services.start_mpesa_payment(invoice=invoice, phone="0712345678")
        services.start_mpesa_payment(invoice=invoice, phone="0712345678")

    assert MpesaPayment.objects.filter(status=MpesaPayment.Status.PENDING).count() == 1
    assert MpesaPayment.objects.get(checkout_request_id="B").status == MpesaPayment.Status.PENDING


# ── the callback, which is where the money is decided ────────────────────────


def apply(body):
    parsed = mpesa.parse_callback(body)
    parsed["raw"] = body
    return services.record_mpesa_result(parsed)


@pytest.fixture
def pending(invoice) -> MpesaPayment:
    with patch("portal.mpesa.access_token", return_value="tok"), \
         patch("portal.mpesa._request", return_value=push_response()):
        return services.start_mpesa_payment(invoice=invoice, phone="0712345678")


def test_a_successful_callback_pays_the_invoice(pending, invoice):
    apply(callback_body())

    invoice.refresh_from_db()
    pending.refresh_from_db()
    assert invoice.status == Invoice.Status.PAID
    assert invoice.payment_reference == "SLJ7XK2P1Q"
    assert invoice.paid_on == timezone.localdate()
    assert pending.status == MpesaPayment.Status.SUCCESS
    # And the milestone follows, as with any other payment.
    assert invoice.milestone.status == Milestone.Status.PAID


def test_a_callback_claiming_a_smaller_amount_does_not_pay_the_invoice(pending, invoice):
    """
    ═══════════════════════════════════════════════════════════════════════════
    THE MOST IMPORTANT TEST IN THIS FILE.

    The callback is unauthenticated. Without this guard a forged body claiming
    one shilling settles a 150,000 shilling invoice, and the only evidence is
    a receipt number nobody checks.
    ═══════════════════════════════════════════════════════════════════════════
    """
    apply(callback_body(amount=1))

    invoice.refresh_from_db()
    pending.refresh_from_db()
    assert invoice.status == Invoice.Status.ISSUED
    assert invoice.payment_reference == ""
    assert pending.status == MpesaPayment.Status.FAILED
    # Recorded in full, because a person needs to look at it.
    assert "mismatch" in pending.result_desc.lower()
    assert pending.receipt == "SLJ7XK2P1Q"


def test_a_cancelled_prompt_is_recorded_and_pays_nothing(pending, invoice):
    apply(callback_body(code=1032))

    invoice.refresh_from_db()
    pending.refresh_from_db()
    assert invoice.status == Invoice.Status.ISSUED
    assert pending.status == MpesaPayment.Status.FAILED
    assert pending.result_code == "1032"


def test_a_replayed_callback_cannot_pay_twice(pending, invoice):
    """Safaricom retries. A retry must not overwrite the receipt or re-apply
    anything."""
    apply(callback_body())
    invoice.refresh_from_db()
    first_paid_on = invoice.paid_on

    apply(callback_body(receipt="DIFFERENT"))

    invoice.refresh_from_db()
    assert invoice.payment_reference == "SLJ7XK2P1Q"
    assert invoice.paid_on == first_paid_on
    assert MpesaPayment.objects.count() == 1


def test_a_callback_for_an_unknown_checkout_id_does_nothing(pending, invoice):
    """
    An attacker cannot invent a CheckoutRequestID: it only ever exists because
    we made a push and Safaricom answered.
    """
    assert apply(callback_body(checkout="ws_CO_FORGED")) is None

    invoice.refresh_from_db()
    assert invoice.status == Invoice.Status.ISSUED


def test_a_callback_with_no_checkout_id_does_nothing():
    assert apply({"Body": {"stkCallback": {}}}) is None


def test_a_failure_callback_with_no_metadata_is_handled(pending):
    """Daraja omits CallbackMetadata entirely on failure. Every read of it has
    to tolerate missing."""
    parsed = mpesa.parse_callback(callback_body(code=1))
    assert parsed["amount"] is None
    assert parsed["receipt"] == ""
    assert parsed["result_code"] == "1"


def test_a_success_against_an_already_paid_invoice_does_not_double_apply(
    pending, invoice, staff
):
    """Someone recorded a bank transfer while the prompt was open. The M-Pesa
    payment stands as a record; the invoice keeps the reference it had."""
    services.record_payment(invoice=invoice, actor=staff, reference="BANK-TRANSFER")

    apply(callback_body())

    invoice.refresh_from_db()
    pending.refresh_from_db()
    assert invoice.payment_reference == "BANK-TRANSFER"
    assert pending.status == MpesaPayment.Status.SUCCESS


# ── the HTTP surface ─────────────────────────────────────────────────────────


def test_the_callback_endpoint_always_answers_200(client, pending, invoice):
    """
    Daraja retries anything that is not a 200 for hours. A retry storm against
    a message we were never going to accept helps nobody, so what we think of
    it goes in the log, not the status code.
    """
    for body in [
        callback_body(),
        {"garbage": True},
        {"Body": {"stkCallback": {"CheckoutRequestID": "nope", "ResultCode": 0}}},
    ]:
        response = client.post(
            reverse("mpesa-callback"), body, content_type="application/json"
        )
        assert response.status_code == 200, body
        assert response.json()["ResultCode"] == 0


def test_a_bad_path_token_changes_nothing(client, settings, pending, invoice):
    settings.MPESA_CALLBACK_TOKEN = "the-real-token"
    response = client.post(
        reverse("mpesa-callback-token", args=["guessed"]),
        callback_body(), content_type="application/json",
    )
    # Still 200 — see the view's docstring — but nothing was applied.
    assert response.status_code == 200
    invoice.refresh_from_db()
    assert invoice.status == Invoice.Status.ISSUED


def test_the_right_path_token_is_applied(client, settings, pending, invoice):
    settings.MPESA_CALLBACK_TOKEN = "the-real-token"
    client.post(
        reverse("mpesa-callback-token", args=["the-real-token"]),
        callback_body(), content_type="application/json",
    )
    invoice.refresh_from_db()
    assert invoice.status == Invoice.Status.PAID


def test_a_client_can_start_a_payment_for_their_own_invoice(client, client_user, invoice):
    client.force_login(client_user)
    with patch("portal.mpesa.access_token", return_value="tok"), \
         patch("portal.mpesa._request", return_value=push_response()):
        response = client.post(
            reverse("invoice-pay", args=[invoice.order.reference, invoice.number]),
            {"phone": "0712345678"}, content_type="application/json",
        )
    assert response.status_code == 202, response.json()
    assert response.json()["phone_tail"] == "5678"


def test_nobody_can_start_a_payment_for_somebody_elses_invoice(client, invoice):
    outsider = User.objects.create_user(
        email="outsider@example.com", password=PASSWORD, email_verified_at=timezone.now()
    )
    client.force_login(outsider)
    response = client.post(
        reverse("invoice-pay", args=[invoice.order.reference, invoice.number]),
        {"phone": "0712345678"}, content_type="application/json",
    )
    assert response.status_code == 404
    assert MpesaPayment.objects.count() == 0


def test_an_anonymous_visitor_cannot_prompt_a_phone(client, invoice):
    response = client.post(
        reverse("invoice-pay", args=[invoice.order.reference, invoice.number]),
        {"phone": "0712345678"}, content_type="application/json",
    )
    assert response.status_code in {401, 403}


def test_the_push_is_throttled(client, client_user, invoice):
    """
    Every push rings a real phone, and the caller can put any number on the
    form. Without a limit this endpoint is a way to make Safaricom interrupt
    somebody repeatedly.
    """
    client.force_login(client_user)
    codes = []
    with patch("portal.mpesa.access_token", return_value="tok"), \
         patch("portal.mpesa._request", side_effect=lambda *a, **k: push_response(
             checkout=f"ws_{len(codes)}")):
        for _ in range(8):
            codes.append(
                client.post(
                    reverse("invoice-pay", args=[invoice.order.reference, invoice.number]),
                    {"phone": "0712345678"}, content_type="application/json",
                ).status_code
            )
    assert 429 in codes


def test_mpesa_being_unconfigured_is_a_clear_refusal(settings, invoice):
    settings.MPESA_ENABLED = False
    with pytest.raises(services.OperationsError) as caught:
        services.start_mpesa_payment(invoice=invoice, phone="0712345678")
    assert "not set up" in str(caught.value)
