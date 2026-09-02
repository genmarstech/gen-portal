"""
Client-facing dashboard API.

Read-only, and every query goes through `selectors.py`. No view here builds its
own Order queryset — that is the single choke point that keeps Organisation A
out of Organisation B's data, and `portal/tests/test_isolation.py` proves it.
"""

from __future__ import annotations

import logging
import secrets

from django.conf import settings
from django.db import transaction
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from django.http import JsonResponse
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts import identity
from accounts.throttling import EnquiryThrottle, MpesaThrottle
from operations import services

from . import mpesa

from .models import Enquiry, Service
from .selectors import (
    export_payload,
    invoice_for,
    live_contract_for,
    order_for,
    orders_for,
)
from .serializers import (
    EnquirySerializer,
    InvoiceDocumentSerializer,
    OnboardingSerializer,
    OrderDetailSerializer,
    OrderListSerializer,
)

log = logging.getLogger(__name__)



class OrderListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        orders = orders_for(request.user)
        return Response(
            {
                "orders": OrderListSerializer(orders, many=True).data,
                # An account with no order is the COMMON case early on — someone
                # signed up, nothing is agreed yet. The client renders an empty
                # state from this, not an error.
                "has_orders": orders.exists(),
                # Whether they have actually told us anything yet. Without this
                # the empty state asked a client who had just filled in a
                # detailed enquiry whether they had talked to us — which reads
                # as nobody having looked at it.
                "has_enquiry": Enquiry.objects.filter(
                    submitted_by=request.user
                ).exists(),
            }
        )


class OrderDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, reference: str):
        order = order_for(request.user, reference)
        if order is None:
            # 404, not 403, for an order that exists but belongs to someone
            # else. A 403 would confirm the reference is real, which turns URL
            # guessing into a client list.
            return Response(
                {"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND
            )
        return Response(OrderDetailSerializer(order).data)


class ExportView(APIView):
    """
    Everything we hold about this account, as JSON.

    Charter 05 §VIII: "We do not hold data, domains, or accounts hostage under
    any circumstance." A portal that cannot hand your data back is exactly the
    hostage-taking the charter rules out, so this ships in v1 rather than being
    a later feature.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        payload = export_payload(request.user)
        response = JsonResponse(payload, json_dumps_params={"indent": 2})
        response["Content-Disposition"] = (
            'attachment; filename="genmars-export.json"'
        )
        return response


class OnboardingView(APIView):
    """
    POST — finish setting up an account: organisation, contact name, enquiry.

    ── WHAT THIS DELIBERATELY DOES NOT DO ──────────────────────────────────────
    It does not create an Order. Charter 02 §I gives qualification to the
    commercial partners and a capacity veto to the founder, so no client-facing
    endpoint may bring an engagement into existence. What it produces is an
    Enquiry: a request, sitting at status NEW, for a human to qualify.

    The whole thing is one transaction. A half-finished onboarding — an
    organisation with no enquiry, or a membership without an organisation — is
    a support ticket nobody can diagnose, because the account looks complete
    from the outside.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        data = OnboardingSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        fields = data.validated_data

        # Verifying the address first is the point of the address. Without this
        # anyone could file enquiries under an address they do not control.
        if not request.user.is_email_verified:
            return Response(
                {"detail": "Verify your email address first."},
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            with transaction.atomic():
                organisation = identity.attach_organisation(
                    request.user, fields["organisation_name"]
                )

                user = request.user
                user.full_name = fields["full_name"].strip()
                user.save(update_fields=["full_name"])

                # Attribution, best effort. An unrecognised slug is dropped
                # rather than refused — the visitor did nothing wrong, and an
                # enquiry that arrives without a service label is far better
                # than one that does not arrive.
                service = None
                if fields["service"]:
                    service = Service.objects.filter(
                        slug=fields["service"], is_active=True
                    ).first()

                Enquiry.objects.create(
                    organisation=organisation,
                    submitted_by=user,
                    problem=fields["problem"],
                    monthly_cost=fields["monthly_cost"],
                    timeline=fields["timeline"],
                    budget_range=fields["budget_range"],
                    service=service,
                    # Kept even when the service did not resolve. "They asked
                    # for Business Setup" is useful on its own, and losing it
                    # because a slug was renamed would be the wrong trade.
                    tier=fields["tier"][:120],
                )
        except identity.AuthError as e:
            # Already onboarded is not an error worth alarming anyone about —
            # it is a double submit, and the account is in the state the caller
            # wanted. Send them on.
            if e.reason == "already_onboarded":
                return Response({"next": "/dashboard"})
            return Response(
                {"detail": e.safe_message}, status=status.HTTP_400_BAD_REQUEST
            )

        return Response({"next": "/dashboard"}, status=status.HTTP_201_CREATED)


class InvoiceDocumentView(APIView):
    """
    One invoice, as a printable document.

    404 for an invoice on someone else's order — the same answer as for one
    that does not exist. Invoice numbers are sequential and therefore guessable,
    so a 403 here would confirm which numbers are real, which is an enumeration
    oracle over how much business Genmars is doing and for whom.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, reference: str, number: str):
        invoice = invoice_for(request.user, reference, number)
        if invoice is None:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        order = invoice.order
        return Response(
            InvoiceDocumentSerializer(
                {
                    "invoice": invoice,
                    "order": order,
                    "contract": live_contract_for(order),
                }
            ).data
        )


class InvoicePayView(APIView):
    """
    Start an M-Pesa prompt for one of this client's own invoices.

    Authenticated and scoped through `invoice_for`, like the document view —
    an STK push costs a real person a real interruption on their phone, so
    "whose invoice is this" is answered before Safaricom is ever called.
    """

    permission_classes = [IsAuthenticated]
    throttle_scope = "mpesa"
    throttle_classes = [MpesaThrottle]

    def post(self, request, reference: str, number: str):
        invoice = invoice_for(request.user, reference, number)
        if invoice is None:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        phone = str(request.data.get("phone") or "").strip()
        if not phone:
            return Response(
                {"detail": "Enter the phone number to prompt.", "field": "phone"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            payment = services.start_mpesa_payment(invoice=invoice, phone=phone)
        except services.OperationsError as exc:
            body = {"detail": exc.message}
            if exc.field:
                body["field"] = exc.field
            return Response(body, status=status.HTTP_400_BAD_REQUEST)
        except mpesa.MpesaError as exc:
            # Safaricom's own message where it is intelligible. Not a 500: the
            # server is fine, the payment did not start.
            return Response({"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)

        return Response(
            {
                "status": payment.status,
                "checkout_request_id": payment.checkout_request_id,
                # So the page can say "check the phone ending 1234" rather than
                # echoing a number the client did not necessarily type.
                "phone_tail": payment.phone[-4:],
                "detail": (
                    "Check that phone for the M-Pesa prompt and enter your PIN. "
                    "This page updates once it goes through."
                ),
            },
            status=status.HTTP_202_ACCEPTED,
        )


class InvoicePaymentStatusView(APIView):
    """
    Has the prompt gone through yet?

    Polled by the invoice page while a prompt is open. Reads our OWN record
    rather than querying Safaricom: the callback is the authority, and a page
    that asked Daraja directly could show "paid" for a payment this system has
    not applied to anything.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, reference: str, number: str):
        invoice = invoice_for(request.user, reference, number)
        if invoice is None:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        payment = invoice.mpesa_payments.first()
        return Response(
            {
                "invoice_status": invoice.status,
                "paid_on": invoice.paid_on,
                "payment_reference": invoice.payment_reference,
                "attempt": (
                    {
                        "status": payment.status,
                        "result_desc": payment.result_desc,
                        "receipt": payment.receipt,
                    }
                    if payment
                    else None
                ),
            }
        )


@method_decorator(csrf_exempt, name="dispatch")
class MpesaCallbackView(APIView):
    """
    Where Safaricom POSTs the result. UNAUTHENTICATED BY NECESSITY.

    ── WHY THIS IS SAFE WITHOUT AUTH ──────────────────────────────────────────

    Daraja sends no signature, no bearer token and no client certificate, and
    it will not follow a redirect to something that does. So the endpoint is
    open by construction, and the defence is that nothing it accepts is
    trusted:

      · the URL carries a shared token, which keeps out anything that has not
        been told the path — not authentication, but it stops drive-by POSTs
      · the body must name a CheckoutRequestID this system created, which an
        attacker cannot invent
      · the amount must match what we asked for, checked in services.py
      · a resolved payment is never re-applied

    It ALWAYS answers 200 with Safaricom's expected shape, including when the
    body is rubbish. Daraja retries anything else for hours, and a retry storm
    against an endpoint that was never going to accept the message helps
    nobody. What we think of the message is recorded in the log, not the status.
    """

    permission_classes = [AllowAny]
    authentication_classes: list = []

    def post(self, request, token: str = ""):
        expected = settings.MPESA_CALLBACK_TOKEN
        if expected and not secrets.compare_digest(token, expected):
            log.warning("mpesa callback with a bad path token")
            # Still 200. See the docstring: a 403 here just buys retries.
            return self._ok()

        try:
            parsed = mpesa.parse_callback(request.data)
            parsed["raw"] = request.data
            services.record_mpesa_result(parsed)
        except Exception:
            # Never let an exception become a non-200. Logged with the traceback
            # so the alert mail fires and somebody looks.
            log.exception("mpesa callback could not be processed")

        return self._ok()

    @staticmethod
    def _ok():
        return Response({"ResultCode": 0, "ResultDesc": "Accepted"})


class EnquiryCreateView(APIView):
    """
    File a new enquiry against an account that already exists.

    The ordering path for returning clients, and the reason it exists is in
    EnquirySerializer: without it, an existing client ordering a second service
    was redirected to their dashboard and the request was thrown away.

    Throttled. An enquiry is cheap to file and costs a human to read, and a
    form that can be submitted in a loop turns into a queue nobody can work.
    """

    permission_classes = [IsAuthenticated]
    throttle_scope = "enquiry"
    throttle_classes = [EnquiryThrottle]

    def post(self, request):
        form = EnquirySerializer(data=request.data)
        form.is_valid(raise_exception=True)
        fields = form.validated_data

        if not request.user.is_email_verified:
            return Response(
                {"detail": "Verify your email address first."},
                status=status.HTTP_403_FORBIDDEN,
            )

        membership = request.user.memberships.select_related("organisation").first()
        if membership is None:
            # Signed in but never onboarded — no organisation to file against.
            # An ordinary state for an invited staff-adjacent account or a
            # half-finished sign-up, so it routes rather than erroring.
            return Response(
                {
                    "detail": "Finish setting up your account first.",
                    "next": "/onboarding",
                },
                status=status.HTTP_409_CONFLICT,
            )

        service = None
        if fields["service"]:
            service = Service.objects.filter(
                slug=fields["service"], is_active=True
            ).first()

        enquiry = Enquiry.objects.create(
            organisation=membership.organisation,
            submitted_by=request.user,
            problem=fields["problem"],
            monthly_cost=fields["monthly_cost"],
            timeline=fields["timeline"],
            budget_range=fields["budget_range"],
            service=service,
            tier=fields["tier"][:120],
        )
        log.info(
            "enquiry %s filed by %s (service=%s tier=%s)",
            enquiry.pk,
            request.user.email,
            service.slug if service else "-",
            fields["tier"] or "-",
        )
        return Response(
            {
                "id": enquiry.pk,
                "organisation": membership.organisation.name,
            },
            status=status.HTTP_201_CREATED,
        )
