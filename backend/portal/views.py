"""
Client-facing dashboard API.

Read-only, and every query goes through `selectors.py`. No view here builds its
own Order queryset — that is the single choke point that keeps Organisation A
out of Organisation B's data, and `portal/tests/test_isolation.py` proves it.
"""

from __future__ import annotations

from django.db import transaction
from django.http import JsonResponse
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts import identity

from .models import Enquiry, Service
from .selectors import (
    export_payload,
    invoice_for,
    live_contract_for,
    order_for,
    orders_for,
)
from .serializers import (
    InvoiceDocumentSerializer,
    OnboardingSerializer,
    OrderDetailSerializer,
    OrderListSerializer,
)


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
