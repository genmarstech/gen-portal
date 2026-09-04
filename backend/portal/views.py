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

from accounts import emails, identity
from accounts.throttling import EnquiryThrottle, MpesaThrottle
from operations import services

from . import mpesa

from django.utils import timezone

from . import selectors
from .models import BillingProfile, Enquiry, Notification, Offer, Service
from .selectors import (
    client_invoice_for,
    export_payload,
    invoice_for,
    live_contract_for,
    order_for,
    orders_for,
)
from .serializers import (
    ClientHostingSerializer,
    OfferDocumentSerializer,
    ClientBlockerSerializer,
    ClientInvoiceSerializer,
    ClientOfferSerializer,
    ClientSystemSerializer,
    ClientTicketSerializer,
    ClientServiceSerializer,
    EnquirySerializer,
    InvoiceDocumentSerializer,
    OnboardingSerializer,
    NotificationSerializer,
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
                "orders": OrderListSerializer(
                    orders, many=True, context={"user": request.user}
                ).data,
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

        # What changed since they last looked, read BEFORE the visit is
        # stamped — otherwise opening the page clears the marker and the page
        # itself never tells them what it was for.
        unseen = selectors.unseen_notice(request.user, order)

        body = OrderDetailSerializer(order).data
        body["unseen"] = unseen

        # And now they have seen it. Only from here: this is the one place
        # somebody has demonstrably read what is on the order, where marking it
        # from a list would clear the marker for one they scrolled past.
        selectors.mark_order_seen(request.user, order)

        return Response(body)


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

        # A record for privacy@, which the privacy policy names. "Who asked for
        # their data, and when" is the first question in any data-protection
        # conversation, and without this the only trace is a log line nobody
        # reads.
        #
        # Wrapped, and deliberately AFTER the payload is built: a client's right
        # to their own data must not depend on a mail provider being up.
        # Charter 05 §VIII.
        try:
            membership = request.user.memberships.select_related(
                "organisation"
            ).first()
            emails.send_data_export_notice(
                email=settings.PRIVACY_EMAIL,
                who=request.user.full_name or request.user.email,
                organisation=(
                    membership.organisation.name if membership else "no organisation"
                ),
                when=timezone.now().strftime("%-d %B %Y, %H:%M UTC"),
            )
        except Exception:
            log.exception("could not record the data export notice")

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


def _resolve_invoice(user, reference: str | None, number: str):
    """
    The invoice this request is about, or None.

    TWO WAYS IN, ONE ANSWER, AND BOTH ARE SCOPED.

    · `/invoices/<number>` — the canonical route. Scoped through the user's
      organisations, which is the only way a DIRECT invoice can be reached at
      all: a renewal or an afternoon's work has no order, so an order-nested
      route cannot address it, and until this existed a client could be sent a
      bill their own portal insisted did not exist.

    · `/orders/<reference>/invoices/<number>` — kept, and kept STRICT. It still
      requires the invoice to belong to that order rather than merely to the
      same client, because a route that quietly ignores half its own path is
      one nobody can reason about later.

    Neither starts from Invoice. An invoice number is sequential and therefore
    guessable, so a query beginning at Invoice would hand one client another's
    billing document the first time somebody forgot a filter.
    """
    if reference:
        return invoice_for(user, reference, number)
    return client_invoice_for(user, number)


class InvoiceDocumentView(APIView):
    """
    One invoice, as a printable document.

    404 for an invoice on someone else's account — the same answer as for one
    that does not exist. Invoice numbers are sequential and therefore guessable,
    so a 403 here would confirm which numbers are real, which is an enumeration
    oracle over how much business Genmars is doing and for whom.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, number: str, reference: str | None = None):
        invoice = _resolve_invoice(request.user, reference, number)
        if invoice is None:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        order = invoice.order
        return Response(
            InvoiceDocumentSerializer(
                {
                    "invoice": invoice,
                    # None for a direct invoice, and the serializer renders the
                    # absence rather than inventing a project.
                    "order": order,
                    "contract": live_contract_for(order) if order else None,
                },
                # Read once per request, not once per field. The profile is a
                # single row and the serializer touches it from two methods.
                context={"billing": BillingProfile.load()},
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

    def post(self, request, number: str, reference: str | None = None):
        invoice = _resolve_invoice(request.user, reference, number)
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

    def get(self, request, number: str, reference: str | None = None):
        invoice = _resolve_invoice(request.user, reference, number)
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
        # Staff see it on the operations surface. Deliberately after the row
        # exists: a notification pointing at nothing is worse than none.
        services.notify_enquiry_received(enquiry)

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


class InvoiceListView(APIView):
    """
    Every invoice addressed to the client, in one place.

    Invoices used to be reachable only through their order. Direct invoices
    have no order, so without this a client could be sent a bill their own
    portal insisted did not exist.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        invoices = selectors.invoices_for(request.user)
        return Response(
            {"invoices": ClientInvoiceSerializer(invoices, many=True).data}
        )


class NotificationListView(APIView):
    """
    The signed-in person's notifications on the CLIENT surface.

    Audience is filtered here and not left to the caller. A staff notification
    is written about internal work — an enquiry arriving, who it was assigned
    to — and must not become readable by passing a query parameter.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        rows = request.user.notifications.filter(
            audience=Notification.Audience.CLIENT
        )[:50]
        unread = request.user.notifications.filter(
            audience=Notification.Audience.CLIENT, read_at__isnull=True
        ).count()
        return Response(
            {
                "notifications": NotificationSerializer(rows, many=True).data,
                "unread": unread,
            }
        )

    def post(self, request):
        """
        Mark as read. One id, or all of them.

        Scoped to `request.user` in the filter itself rather than checked
        afterwards, so an id belonging to someone else matches nothing instead
        of being found and then rejected.
        """
        pk = request.data.get("id")
        rows = request.user.notifications.filter(
            audience=Notification.Audience.CLIENT, read_at__isnull=True
        )
        if pk is not None:
            rows = rows.filter(pk=pk)

        rows.update(read_at=timezone.now())
        unread = request.user.notifications.filter(
            audience=Notification.Audience.CLIENT, read_at__isnull=True
        ).count()
        return Response({"unread": unread})


class ServiceCatalogueView(APIView):
    """
    The catalogue, so a client can order without leaving the portal.

    Active services only. An inactive one is something we have stopped
    selling, and listing it invites an order we would have to refuse — Charter
    04 §IV, in the smallest possible form.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        services_qs = (
            Service.objects.filter(is_active=True)
            .prefetch_related("tiers")
            .order_by("name")
        )
        return Response(
            {"services": ClientServiceSerializer(services_qs, many=True).data}
        )


class OfferDocumentView(APIView):
    """
    One offer, as a printable document.

    404 for an offer on somebody else's account — the same answer as for one
    that does not exist. Offer references are sequential and therefore
    guessable, so a 403 would confirm which are real, turning this into a
    counter of how much Genmars is quoting and to whom.

    A DRAFT is never reachable here: `offers_for` excludes them, so an offer we
    have not decided to send cannot be opened by guessing its reference the day
    before we send it.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, reference: str):
        offer = selectors.offer_for(request.user, reference)
        if offer is None:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        return Response(
            OfferDocumentSerializer(
                {"offer": offer},
                context={"billing": BillingProfile.load()},
            ).data
        )


class OfferListView(APIView):
    """
    Offers put to this client.

    Drafts are excluded: an offer we have not sent is not one they have
    received, and showing it would put a price in front of somebody before we
    had decided to.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        # Scoped in portal/selectors.py, like every other client read. It used
        # to be filtered here, which is exactly the thing that module exists to
        # stop: one view's copy of a filter cannot be kept in step with another.
        return Response(
            {"offers": ClientOfferSerializer(selectors.offers_for(request.user), many=True).data}
        )


class OfferDecisionView(APIView):
    """
    Accept or decline.

    Scoped through the user's organisations in the lookup itself, so an offer
    reference belonging to someone else matches nothing rather than being found
    and then refused.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request, reference: str):
        offer = (
            Offer.objects.filter(
                reference=reference,
                organisation_id__in=selectors.organisation_ids_for(request.user),
            )
            .exclude(status=Offer.Status.DRAFT)
            .first()
        )
        if offer is None:
            return Response(
                {"detail": "No such offer."}, status=status.HTTP_404_NOT_FOUND
            )

        decision = request.data.get("decision")
        try:
            if decision == "accept":
                offer = services.accept_offer(offer=offer, actor=request.user)
            elif decision == "decline":
                offer = services.decline_offer(
                    offer=offer,
                    actor=request.user,
                    reason=request.data.get("reason", ""),
                )
            else:
                return Response(
                    {"detail": "Say whether you are accepting or declining."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        except services.OperationsError as exc:
            return Response(
                {"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST
            )

        return Response(ClientOfferSerializer(offer).data)


class SupportView(APIView):
    """
    The client's own support requests, and raising one.

    Throttled on the same scope as enquiries: a request is cheap to file and
    costs a person to read, and a form that can be submitted in a loop turns
    into a queue nobody can work.
    """

    permission_classes = [IsAuthenticated]
    throttle_scope = "enquiry"
    throttle_classes = [EnquiryThrottle]

    def get(self, request):
        tickets = selectors.tickets_for(request.user)
        return Response(
            {
                "tickets": ClientTicketSerializer(
                    tickets, many=True, context={"user": request.user}
                ).data
            }
        )

    def post(self, request):
        membership = request.user.memberships.select_related("organisation").first()
        if membership is None:
            return Response(
                {"detail": "Finish setting up your account first.",
                 "next": "/onboarding"},
                status=status.HTTP_409_CONFLICT,
            )

        order = None
        reference = str(request.data.get("order", "")).strip()
        if reference:
            order = selectors.order_for(request.user, reference)

        try:
            ticket = services.raise_ticket(
                organisation=membership.organisation,
                actor=request.user,
                subject=str(request.data.get("subject", "")),
                body=str(request.data.get("body", "")),
                order=order,
            )
        except services.OperationsError as exc:
            return Response(
                {"detail": str(exc), "field": exc.field},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            ClientTicketSerializer(ticket, context={"user": request.user}).data,
            status=status.HTTP_201_CREATED,
        )


class SupportReplyView(APIView):
    """
    Add a message to one of the client's own tickets.

    The ticket is looked up THROUGH the user's organisations, so a reference
    belonging to someone else matches nothing rather than being found and then
    refused.
    """

    permission_classes = [IsAuthenticated]
    throttle_scope = "enquiry"
    throttle_classes = [EnquiryThrottle]

    def post(self, request, reference: str):
        ticket = selectors.ticket_for(request.user, reference)
        if ticket is None:
            return Response(
                {"detail": "No such request."}, status=status.HTTP_404_NOT_FOUND
            )

        try:
            services.reply_to_ticket(
                ticket=ticket,
                actor=request.user,
                body=str(request.data.get("body", "")),
                # Never taken from the request on this endpoint. A client must
                # not be able to write a note hidden from themselves, and the
                # flag must never be settable by the side it hides from.
                internal=False,
            )
        except services.OperationsError as exc:
            return Response(
                {"detail": str(exc), "field": exc.field},
                status=status.HTTP_400_BAD_REQUEST,
            )

        ticket.refresh_from_db()
        return Response(
            ClientTicketSerializer(ticket, context={"user": request.user}).data
        )


class DashboardView(APIView):
    """
    The two things a client can act on, in one call.

    ── WHY THESE TWO AND NOT A WALL OF FIGURES ─────────────────────────────────

    A dashboard that shows everything is a dashboard nobody reads twice. These
    are the only two things on it that change what somebody DOES:

      · what we are waiting on them for, which is the commonest reason a
        project quietly stops moving; and
      · whether the thing we run for them is up, which is the question they
        would otherwise be asking us by email.

    Everything else they might want — invoices, offers, progress notes — has
    its own page and is better there.

    ── ONE CALL, BECAUSE IT IS ONE SCREEN ──────────────────────────────────────

    Two round trips to paint one view is two chances for half of it to arrive.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        blockers = selectors.waiting_on_client(request.user)
        systems = selectors.systems_for(request.user)

        return Response(
            {
                "waiting_on_you": ClientBlockerSerializer(blockers, many=True).data,
                "systems": ClientSystemSerializer(systems, many=True).data,
                # ── what is still running ────────────────────────────────────
                #
                # Once past work started being recorded here, a client's order
                # list became things finished years ago mixed with things still
                # live, in one undifferentiated column — and "is this retainer
                # still running" was answerable only by reading every row.
                "ongoing": OrderListSerializer(
                    selectors.ongoing_work_for(request.user),
                    many=True,
                    context={"user": request.user},
                ).data,
                # Charter 05 §VIII. A promise that we do not hold domains
                # hostage is worth little if the client cannot see which of
                # their accounts is in our name — see ClientHostingSerializer
                # for what this deliberately leaves out.
                "hosting": ClientHostingSerializer(
                    selectors.hosting_for_client(request.user), many=True
                ).data,
            }
        )

