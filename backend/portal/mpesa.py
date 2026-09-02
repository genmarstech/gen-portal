"""
M-Pesa STK push, against Safaricom's Daraja API.

── NO NEW DEPENDENCY ────────────────────────────────────────────────────────
Charter 03 §I. There are several `django-daraja`-style packages; this is two
JSON POSTs and a base64 string, which urllib has done since Python 2. Nothing
third-party goes in the path that money travels down.

── WHAT THIS FILE MUST NEVER DO ─────────────────────────────────────────────
**Never log the consumer secret, the passkey, or the generated password.** The
password is base64 of the passkey and is a live credential; a log line carrying
it is the same leak as the passkey itself. Errors log the HTTP status,
Safaricom's own error text, and the invoice — never the request body.

**Never mark an invoice paid from this module.** Everything here does is ask
Safaricom to prompt a phone. Whether money actually moved is decided by the
callback, and only by the callback: the STK response means "we sent the
prompt", not "they paid", and treating the two as the same is how a system
marks an invoice settled that nobody ever paid.
"""

from __future__ import annotations

import base64
import json
import logging
import urllib.error
import urllib.request
from datetime import datetime

from django.conf import settings
from django.utils import timezone

log = logging.getLogger(__name__)

TIMEOUT = 30
USER_AGENT = "gen-portal (+https://genmars.co.ke)"


class MpesaError(Exception):
    """Something went wrong talking to Daraja. The message is safe to show."""


def normalise_phone(raw: str) -> str:
    """
    Kenyan mobile number to the 2547XXXXXXXX / 2541XXXXXXXX form Daraja wants.

    Accepts what people actually type: 0712..., +254712..., 254712...,
    712..., and any of those with spaces or dashes. Daraja rejects anything
    else with an unhelpful error, and a customer who typed their own number
    correctly should never see it.
    """
    digits = "".join(c for c in raw if c.isdigit())

    if digits.startswith("254"):
        pass
    elif digits.startswith("0"):
        digits = "254" + digits[1:]
    elif len(digits) == 9 and digits[0] in "17":
        # Typed without the leading zero.
        digits = "254" + digits
    else:
        raise MpesaError("That does not look like a Kenyan mobile number.")

    # 254 + 9 digits, and Safaricom/Airtel mobile prefixes are 7 or 1.
    if len(digits) != 12 or digits[3] not in "17":
        raise MpesaError("That does not look like a Kenyan mobile number.")

    return digits


def _timestamp(now: datetime | None = None) -> str:
    return (now or timezone.localtime()).strftime("%Y%m%d%H%M%S")


def _password(timestamp: str) -> str:
    """
    base64(BusinessShortCode + Passkey + Timestamp).

    The SHORT CODE, not the till. On a Buy Goods setup those are different
    numbers, and using the till here produces an invalid-credential error that
    reads as though the passkey is wrong — hours of debugging the wrong thing.
    """
    raw = f"{settings.MPESA_SHORT_CODE}{settings.MPESA_CONSUMER_PASSKEY}{timestamp}"
    return base64.b64encode(raw.encode()).decode()


def _request(url: str, *, data: bytes | None, headers: dict[str, str]) -> dict:
    req = urllib.request.Request(url, data=data, headers={**headers, "User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")[:400]
        # Safaricom's own message, which is usually specific. Logged without
        # the request body, which carries the password.
        log.error("daraja %s -> %s %s", url, exc.code, body)
        raise MpesaError(_readable(exc.code, body)) from exc
    except urllib.error.URLError as exc:
        log.error("daraja %s unreachable: %s", url, exc.reason)
        raise MpesaError("Could not reach M-Pesa just now. Try again in a moment.") from exc
    except json.JSONDecodeError as exc:
        log.error("daraja %s returned non-JSON", url)
        raise MpesaError("M-Pesa returned something unexpected.") from exc


def _readable(status: int, body: str) -> str:
    """
    Safaricom's error, or a plain sentence when it is unintelligible.

    Their errorMessage is usually the most useful thing available, so it is
    preferred over anything invented here — but it is not shown raw for 5xx,
    where it tends to be a stack-trace-ish string that alarms a client.
    """
    try:
        parsed = json.loads(body)
        message = parsed.get("errorMessage") or parsed.get("ResponseDescription")
    except Exception:
        message = None

    if status >= 500 or not message:
        return "M-Pesa is not responding just now. Try again in a moment."
    return str(message)


def access_token() -> str:
    """
    OAuth token. Short-lived (Daraja says an hour) and cheap, so it is fetched
    per push rather than cached — a stale token fails a payment, and the
    round trip costs less than the class of bug that caching introduces here.
    """
    credentials = base64.b64encode(
        f"{settings.MPESA_CONSUMER_KEY}:{settings.MPESA_CONSUMER_SECRET}".encode()
    ).decode()

    payload = _request(
        f"{settings.MPESA_BASE_URL}/oauth/v1/generate?grant_type=client_credentials",
        data=None,
        headers={"Authorization": f"Basic {credentials}"},
    )
    token = payload.get("access_token")
    if not token:
        raise MpesaError("M-Pesa did not return an access token.")
    return token


def stk_push(*, phone: str, amount: int, reference: str, description: str) -> dict:
    """
    Ask Safaricom to prompt `phone` for `amount` shillings.

    Returns Daraja's response, which carries CheckoutRequestID — the only
    handle that ties the eventual callback back to this request.

    `amount` is an INT because Daraja's Amount is whole shillings. The caller
    decides what to do about an invoice with cents; silently rounding money
    here would produce a payment that does not match any invoice.
    """
    if not settings.MPESA_ENABLED:
        raise MpesaError("M-Pesa is not configured on this server.")
    if amount < 1:
        raise MpesaError("M-Pesa cannot take an amount below one shilling.")

    timestamp = _timestamp()
    body = {
        "BusinessShortCode": settings.MPESA_SHORT_CODE,
        "Password": _password(timestamp),
        "Timestamp": timestamp,
        "TransactionType": settings.MPESA_TRANSACTION_TYPE,
        "Amount": amount,
        "PartyA": phone,
        # The till for Buy Goods, the paybill for PayBill. See settings.py.
        "PartyB": settings.MPESA_TILL_NUMBER,
        "PhoneNumber": phone,
        "CallBackURL": settings.MPESA_CALLBACK_URL,
        # Shown on the customer's phone and on their statement. The invoice
        # number, so what they paid is identifiable from their own records.
        "AccountReference": reference[:12],
        "TransactionDesc": description[:13],
    }

    payload = _request(
        f"{settings.MPESA_BASE_URL}/mpesa/stkpush/v1/processrequest",
        data=json.dumps(body).encode(),
        headers={
            "Authorization": f"Bearer {access_token()}",
            "Content-Type": "application/json",
        },
    )

    # ResponseCode "0" means the prompt was accepted for delivery. It does NOT
    # mean anybody paid.
    if str(payload.get("ResponseCode")) != "0":
        raise MpesaError(
            payload.get("ResponseDescription") or "M-Pesa refused the request."
        )
    return payload


def parse_callback(body: dict) -> dict:
    """
    Flatten Daraja's callback into something usable.

    Their shape is
      Body.stkCallback.{MerchantRequestID, CheckoutRequestID, ResultCode,
                        ResultDesc, CallbackMetadata.Item[]}
    where Item is a list of {Name, Value} pairs that is ABSENT on failure —
    a cancelled or timed-out prompt has no metadata at all, so every read of
    it has to tolerate missing.
    """
    callback = (body.get("Body") or {}).get("stkCallback") or {}
    items = (callback.get("CallbackMetadata") or {}).get("Item") or []
    values = {item.get("Name"): item.get("Value") for item in items if item.get("Name")}

    return {
        "merchant_request_id": callback.get("MerchantRequestID") or "",
        "checkout_request_id": callback.get("CheckoutRequestID") or "",
        # A STRING code, because Daraja is inconsistent about int vs str.
        "result_code": str(callback.get("ResultCode", "")),
        "result_desc": callback.get("ResultDesc") or "",
        "amount": values.get("Amount"),
        "receipt": values.get("MpesaReceiptNumber") or "",
        "phone": str(values.get("PhoneNumber") or ""),
    }
