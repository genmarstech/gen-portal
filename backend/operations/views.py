"""
Operations API — the staff side of the same rows the client portal shows.

Mounted under /api/ops/. Every view is IsStaff; there is no per-view exception
and there must not be one. A single view that forgets it reads every
organisation's data to whoever asks.

Writes go through `services.py` rather than being done inline, so the rules
about converting, declining and publishing live in one place instead of being
re-implemented slightly differently by the next endpoint.
"""

from __future__ import annotations

from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status as http
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import Membership, Organisation, User
from portal.models import (
    Blocker,
    Contract,
    DeliveryGate,
    Invoice,
    Milestone,
    Notification,
    Order,
    ProgressNote,
    Service,
)

from . import selectors, services
from .permissions import CanCommit, CanManageAccess, CanQualify, IsStaff
from .serializers import (
    DirectInvoiceSerializer,
    InvoiceSerializer,
    NotificationSerializer,
    InvoiceWriteSerializer,
    PaymentSerializer,
    VoidInvoiceSerializer,
    BlockerSerializer,
    StaffInviteSerializer,
    StaffWriteSerializer,
    TeamMemberSerializer,
    ContractSerializer,
    IssueContractSerializer,
    ServiceSerializer,
    ServiceWriteSerializer,
    SignatureSerializer,
    VoidSerializer,
    InviteSerializer,
    MembershipSerializer,
    MembershipWriteSerializer,
    OrganisationSerializer,
    OrganisationWriteSerializer,
    BlockerWriteSerializer,
    ConvertSerializer,
    DeliveryGateSerializer,
    GateWriteSerializer,
    DecideSerializer,
    EnquiryDetailSerializer,
    EnquiryListSerializer,
    MilestoneSerializer,
    OrderDetailSerializer,
    OrderListSerializer,
    OrderWriteSerializer,
    ProgressNoteSerializer,
)


class StaffView(APIView):
    """Base class, so `permission_classes` cannot be forgotten on a new view."""

    permission_classes = [IsStaff]


def _refuse(exc: services.OperationsError):
    body = {"detail": exc.message}
    if exc.field:
        body["field"] = exc.field
    return Response(body, status=http.HTTP_400_BAD_REQUEST)


# ── the queue ────────────────────────────────────────────────────────────────


class OverviewView(StaffView):
    """The dashboard header: what needs attention, as numbers."""

    def get(self, request):
        return Response(
            {
                "counts": selectors.queue_counts(),
                "me": {"full_name": request.user.full_name, "email": request.user.email},
            }
        )


class EnquiryListView(StaffView):
    def get(self, request):
        state = request.query_params.get("status")
        qs = selectors.open_enquiries() if state == "open" else selectors.enquiries(status=state or None)
        return Response({"enquiries": EnquiryListSerializer(qs, many=True).data})


class EnquiryDetailView(StaffView):

    permission_classes = [CanQualify]
    def get(self, request, pk: int):
        enquiry = selectors.enquiry(pk)
        if enquiry is None:
            return Response({"detail": "No such enquiry."}, status=http.HTTP_404_NOT_FOUND)
        return Response(EnquiryDetailSerializer(enquiry).data)

    def post(self, request, pk: int):
        """Triage: qualify, decline, or put back. Not convert — that is its own
        endpoint, because it creates an order."""
        enquiry = selectors.enquiry(pk)
        if enquiry is None:
            return Response({"detail": "No such enquiry."}, status=http.HTTP_404_NOT_FOUND)

        data = DecideSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        try:
            enquiry = services.decide_enquiry(
                enquiry=enquiry,
                actor=request.user,
                status=data.validated_data["status"],
                note=data.validated_data["note"],
            )
        except services.OperationsError as exc:
            return _refuse(exc)
        return Response(EnquiryDetailSerializer(enquiry).data)


class EnquiryConvertView(StaffView):

    permission_classes = [CanQualify]
    def post(self, request, pk: int):
        enquiry = selectors.enquiry(pk)
        if enquiry is None:
            return Response({"detail": "No such enquiry."}, status=http.HTTP_404_NOT_FOUND)

        data = ConvertSerializer(data=request.data)
        data.is_valid(raise_exception=True)

        contact = None
        contact_id = data.validated_data.get("contact")
        if contact_id:
            contact = User.objects.filter(pk=contact_id).first()
            if contact is None:
                return Response(
                    {"detail": "No such account.", "field": "contact"},
                    status=http.HTTP_400_BAD_REQUEST,
                )

        service = None
        service_id = data.validated_data.get("service")
        if service_id:
            service = Service.objects.filter(pk=service_id).first()
            if service is None:
                return Response(
                    {"detail": "No such service.", "field": "service"},
                    status=http.HTTP_400_BAD_REQUEST,
                )

        try:
            order = services.convert_enquiry(
                enquiry=enquiry,
                actor=request.user,
                title=data.validated_data["title"],
                scope=data.validated_data["scope"],
                exclusions=data.validated_data["exclusions"],
                contact=contact,
                target_date=data.validated_data.get("target_date"),
                service=service,
            )
        except services.OperationsError as exc:
            return _refuse(exc)

        return Response(
            OrderDetailSerializer(selectors.order(order.reference)).data,
            status=http.HTTP_201_CREATED,
        )


# ── orders ───────────────────────────────────────────────────────────────────


class OrderListView(StaffView):
    def get(self, request):
        return Response({"orders": OrderListSerializer(selectors.orders(), many=True).data})


class OrderDetailView(StaffView):
    def get(self, request, reference: str):
        order = selectors.order(reference)
        if order is None:
            return Response({"detail": "No such order."}, status=http.HTTP_404_NOT_FOUND)
        return Response(OrderDetailSerializer(order).data)

    def patch(self, request, reference: str):
        order = get_object_or_404(Order, reference=reference)
        form = OrderWriteSerializer(order, data=request.data, partial=True)
        form.is_valid(raise_exception=True)
        form.save()
        return Response(OrderDetailSerializer(selectors.order(reference)).data)


class OrderNoteView(StaffView):
    """
    Weekly notes. Charter 05 §III.

    POST drafts one (or updates the existing draft for that week — `week_of` is
    unique per order, and a second POST for the same week is someone continuing
    to write, not a conflict to reject).
    """

    def post(self, request, reference: str):
        order = get_object_or_404(Order, reference=reference)
        form = ProgressNoteSerializer(data=request.data)
        form.is_valid(raise_exception=True)

        existing = order.notes.filter(week_of=form.validated_data["week_of"]).first()
        if existing and existing.is_published:
            return Response(
                {
                    "detail": (
                        "That week's note is published and cannot be edited. "
                        "Write a correction as a new note."
                    ),
                    "field": "week_of",
                },
                status=http.HTTP_400_BAD_REQUEST,
            )

        if existing:
            existing.body = form.validated_data["body"]
            existing.save(update_fields=["body"])
            note = existing
        else:
            note = ProgressNote.objects.create(
                order=order,
                author=request.user,
                week_of=form.validated_data["week_of"],
                body=form.validated_data["body"],
            )

        if str(request.data.get("publish", "")).lower() in {"1", "true", "yes"}:
            services.publish_note(note=note)

        note.refresh_from_db()
        return Response(
            ProgressNoteSerializer(note).data, status=http.HTTP_201_CREATED
        )


class NotePublishView(StaffView):
    def post(self, request, reference: str, pk: int):
        note = get_object_or_404(ProgressNote, pk=pk, order__reference=reference)
        services.publish_note(note=note)
        return Response(ProgressNoteSerializer(note).data)


class OrderMilestoneView(StaffView):
    def post(self, request, reference: str):
        order = get_object_or_404(Order, reference=reference)
        form = MilestoneSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        form.save(order=order)
        return Response(form.data, status=http.HTTP_201_CREATED)


class MilestoneDetailView(StaffView):
    def patch(self, request, reference: str, pk: int):
        milestone = get_object_or_404(Milestone, pk=pk, order__reference=reference)
        form = MilestoneSerializer(milestone, data=request.data, partial=True)
        form.is_valid(raise_exception=True)
        form.save()

        # `paid_at` is set by the model, not by the client — a paid date the
        # browser can choose is a paid date nobody can reconcile against a bank
        # statement.
        if form.validated_data.get("status") == Milestone.Status.PAID:
            milestone.refresh_from_db()
            if milestone.paid_at is None:
                milestone.mark_paid()

        milestone.refresh_from_db()
        return Response(MilestoneSerializer(milestone).data)


class StaffDirectoryView(StaffView):
    """
    The team. Readable by every staff account, changeable by a founder.

    Also feeds the "named contact" picker, which is why it stays readable to
    everyone: choosing who a client escalates to is ordinary work, not an
    access decision.

    Inactive accounts are included and flagged rather than filtered out. A
    deactivated colleague still authored notes and issued contracts, and a
    directory that hides them makes those look authorless.
    """

    def get(self, request):
        people = User.objects.filter(is_staff=True).order_by(
            "-is_active", "full_name", "email"
        )
        return Response(
            {
                "staff": TeamMemberSerializer(people, many=True).data,
                # What the CURRENT user may do, so the UI can hide controls it
                # would only get a 403 from. The server is still the authority;
                # this exists so the screen does not offer what it cannot do.
                "me": {
                    "id": request.user.pk,
                    "email": request.user.email,
                    "staff_role": request.user.staff_role,
                    "can_qualify": request.user.can_qualify,
                    "can_commit": request.user.can_commit,
                    "can_manage_access": request.user.can_manage_access,
                },
            }
        )

    def post(self, request):
        """Invite a colleague. Founder only."""
        if not request.user.can_manage_access:
            return Response(
                {"detail": "Only a founder can add someone to the team."},
                status=http.HTTP_403_FORBIDDEN,
            )
        form = StaffInviteSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        try:
            user, _ = services.invite_staff(
                actor=request.user,
                email=form.validated_data["email"],
                full_name=form.validated_data["full_name"],
                role=form.validated_data["role"],
            )
        except services.OperationsError as exc:
            return _refuse(exc)
        return Response(TeamMemberSerializer(user).data, status=http.HTTP_201_CREATED)


class StaffDetailView(StaffView):
    """Change a colleague's role, or revoke their access. Founder only."""

    permission_classes = [CanManageAccess]

    def patch(self, request, pk: int):
        user = get_object_or_404(User, pk=pk, is_staff=True)
        form = StaffWriteSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        try:
            if "role" in form.validated_data:
                user = services.set_staff_role(
                    actor=request.user, user=user, role=form.validated_data["role"]
                )
            if "is_active" in form.validated_data:
                user = services.set_staff_active(
                    actor=request.user,
                    user=user,
                    active=form.validated_data["is_active"],
                )
        except services.OperationsError as exc:
            return _refuse(exc)
        return Response(TeamMemberSerializer(user).data)


# ── engineering delivery ─────────────────────────────────────────────────────


class DeliveryBoardView(StaffView):
    """
    What is blocked, and what is not done. The two questions worth a board.

    Ordered blocked-first then least-complete by the selector, so this opens on
    the work most at risk rather than the most recent.
    """

    def get(self, request):
        return Response(
            {
                "counts": selectors.delivery_counts(),
                "orders": selectors.delivery_overview(),
            }
        )


class GateView(StaffView):
    def post(self, request, reference: str, pk: int):
        gate = get_object_or_404(DeliveryGate, pk=pk, order__reference=reference)
        form = GateWriteSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        try:
            gate = services.set_gate(
                gate=gate,
                actor=request.user,
                met=form.validated_data["met"],
                note=form.validated_data["note"],
            )
        except services.OperationsError as exc:
            return _refuse(exc)
        return Response(DeliveryGateSerializer(gate).data)


class BlockerListView(StaffView):
    def post(self, request, reference: str):
        order = get_object_or_404(Order, reference=reference)
        form = BlockerWriteSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        try:
            blocker = services.raise_blocker(
                order=order,
                actor=request.user,
                summary=form.validated_data["summary"],
                detail=form.validated_data["detail"],
                waiting_on=form.validated_data["waiting_on"],
            )
        except services.OperationsError as exc:
            return _refuse(exc)
        return Response(BlockerSerializer(blocker).data, status=http.HTTP_201_CREATED)


class BlockerDetailView(StaffView):
    def post(self, request, reference: str, pk: int):
        """Clear it. There is no un-clear: a blocker that came back is a new
        blocker, and collapsing the two loses that it happened twice."""
        blocker = get_object_or_404(Blocker, pk=pk, order__reference=reference)
        blocker = services.clear_blocker(
            blocker=blocker, resolution=request.data.get("resolution", "")
        )
        return Response(BlockerSerializer(blocker).data)


class BackfillGatesView(StaffView):
    """
    Give existing orders their gates.

    Orders created before delivery gates existed have none. This is idempotent
    (get_or_create), so it is safe to call repeatedly and safe to forget whether
    it has been called.
    """

    def post(self, request):
        created = 0
        for order in Order.objects.all():
            before = order.gates.count()
            services.create_delivery_gates(order=order)
            created += order.gates.count() - before
        return Response({"gates_created": created})


# ── client accounts ──────────────────────────────────────────────────────────


class OrganisationListView(StaffView):

    permission_classes = [CanManageAccess]
    def get(self, request):
        return Response(
            {"organisations": OrganisationSerializer(selectors.organisations(), many=True).data}
        )

    def post(self, request):
        form = OrganisationWriteSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        try:
            org = services.create_organisation(name=form.validated_data["name"])
        except services.OperationsError as exc:
            return _refuse(exc)
        return Response(
            OrganisationSerializer(selectors.organisation(org.pk)).data,
            status=http.HTTP_201_CREATED,
        )


class OrganisationMembersView(StaffView):

    permission_classes = [CanManageAccess]
    def post(self, request, pk: int):
        org = selectors.organisation(pk)
        if org is None:
            return Response({"detail": "No such organisation."}, status=http.HTTP_404_NOT_FOUND)

        form = InviteSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        try:
            membership, invited = services.invite_to_organisation(
                organisation=org,
                actor=request.user,
                email=form.validated_data["email"],
                full_name=form.validated_data["full_name"],
                role=form.validated_data["role"],
            )
        except services.OperationsError as exc:
            return _refuse(exc)

        return Response(
            {
                "membership": MembershipSerializer(membership).data,
                # False when the account already existed, so the UI can say
                # "added" rather than "invited" — no code was sent, and telling
                # someone to check their inbox for nothing is worse than saying
                # nothing.
                "invited": invited,
            },
            status=http.HTTP_201_CREATED,
        )


class MembershipDetailView(StaffView):

    permission_classes = [CanManageAccess]
    def patch(self, request, pk: int):
        membership = get_object_or_404(Membership, pk=pk)
        form = MembershipWriteSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        try:
            membership = services.update_membership(
                membership=membership,
                role=form.validated_data.get("role"),
                receives_updates=form.validated_data.get("receives_updates"),
            )
        except services.OperationsError as exc:
            return _refuse(exc)
        return Response(MembershipSerializer(membership).data)

    def delete(self, request, pk: int):
        membership = get_object_or_404(Membership, pk=pk)
        services.remove_membership(membership=membership)
        return Response(status=http.HTTP_204_NO_CONTENT)


# ── services and contracts ───────────────────────────────────────────────────


class ServiceListView(StaffView):

    permission_classes = [CanCommit]
    def get(self, request):
        return Response({"services": ServiceSerializer(selectors.services(), many=True).data})

    def post(self, request):
        form = ServiceWriteSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        try:
            service = services.upsert_service(**form.validated_data)
        except services.OperationsError as exc:
            return _refuse(exc)
        return Response(ServiceSerializer(service).data, status=http.HTTP_201_CREATED)


class ServiceDetailView(StaffView):

    permission_classes = [CanCommit]
    def patch(self, request, pk: int):
        service = get_object_or_404(Service, pk=pk)
        form = ServiceWriteSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        try:
            service = services.upsert_service(service=service, **form.validated_data)
        except services.OperationsError as exc:
            return _refuse(exc)
        return Response(ServiceSerializer(service).data)


class ContractListView(StaffView):

    permission_classes = [CanCommit]
    """Issue a new version. There is no PUT — an issued contract is frozen."""

    def post(self, request, reference: str):
        order = get_object_or_404(Order, reference=reference)
        form = IssueContractSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        try:
            contract = services.issue_contract(
                order=order,
                actor=request.user,
                deliverables=form.validated_data["deliverables"],
            )
        except services.OperationsError as exc:
            return _refuse(exc)
        return Response(ContractSerializer(contract).data, status=http.HTTP_201_CREATED)


class ContractSignView(StaffView):

    permission_classes = [CanCommit]
    def post(self, request, reference: str, pk: int):
        contract = get_object_or_404(Contract, pk=pk, order__reference=reference)
        form = SignatureSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        try:
            contract = services.record_signature(
                contract=contract,
                actor=request.user,
                signed_on=form.validated_data["signed_on"],
                signed_by_name=form.validated_data["signed_by_name"],
                note=form.validated_data["note"],
            )
        except services.OperationsError as exc:
            return _refuse(exc)
        return Response(ContractSerializer(contract).data)


class ContractVoidView(StaffView):

    permission_classes = [CanCommit]
    def post(self, request, reference: str, pk: int):
        contract = get_object_or_404(Contract, pk=pk, order__reference=reference)
        form = VoidSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        try:
            contract = services.void_contract(
                contract=contract, reason=form.validated_data["reason"]
            )
        except services.OperationsError as exc:
            return _refuse(exc)
        return Response(ContractSerializer(contract).data)


class InvoiceListView(StaffView):
    """
    The invoices on an order, and issuing one.

    CanCommit, not IsStaff. Billing is the same authority as pricing and
    signing — Charter 02 §I keeps money with the founder and the commercial
    partners, and an invoice is the moment that decision reaches a client's
    accounts department.
    """

    permission_classes = [CanCommit]

    def get(self, request, reference: str):
        order = get_object_or_404(Order, reference=reference)
        invoices = order.invoices.select_related("milestone", "issued_by")
        return Response({"invoices": InvoiceSerializer(invoices, many=True).data})

    def post(self, request, reference: str):
        order = get_object_or_404(Order, reference=reference)
        form = InvoiceWriteSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        try:
            invoice = services.issue_invoice(
                order=order,
                actor=request.user,
                milestone=form.validated_data["milestone"],
                description=form.validated_data["description"],
                amount_kes=form.validated_data["amount_kes"],
                due_on=form.validated_data["due_on"],
                issued_on=form.validated_data["issued_on"],
            )
        except services.OperationsError as exc:
            return _refuse(exc)
        return Response(InvoiceSerializer(invoice).data, status=http.HTTP_201_CREATED)


class InvoicePaymentView(StaffView):
    """Record that money arrived. It does not move any — see Invoice's docstring."""

    permission_classes = [CanCommit]

    def post(self, request, reference: str, pk: int):
        invoice = get_object_or_404(Invoice, pk=pk, order__reference=reference)
        form = PaymentSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        try:
            invoice = services.record_payment(
                invoice=invoice,
                actor=request.user,
                amount_kes=form.validated_data["amount_kes"],
                method=form.validated_data["method"],
                reference=form.validated_data["reference"],
                paid_on=form.validated_data["paid_on"],
                note=form.validated_data["note"],
            )
        except services.OperationsError as exc:
            return _refuse(exc)
        return Response(InvoiceSerializer(invoice).data)


class InvoiceVoidView(StaffView):
    """Withdraw an invoice that should not have been sent."""

    permission_classes = [CanCommit]

    def post(self, request, reference: str, pk: int):
        invoice = get_object_or_404(Invoice, pk=pk, order__reference=reference)
        form = VoidInvoiceSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        try:
            invoice = services.void_invoice(
                invoice=invoice, actor=request.user, reason=form.validated_data["reason"]
            )
        except services.OperationsError as exc:
            return _refuse(exc)
        return Response(InvoiceSerializer(invoice).data)


# ── invoices that are not attached to an order ───────────────────────────────
#
# The views above are all nested under an order, which was right while every
# invoice had one. These are the flat equivalents. They work for EVERY invoice,
# direct or not, so operations has one place to look rather than having to know
# which kind it is dealing with before it can find it.


class AllInvoiceListView(StaffView):
    """
    Every invoice in the company, and raising one straight to a client.

    CanCommit, like the nested view. Billing is the same authority as pricing
    and signing — Charter 02 §I keeps money with the founder and the commercial
    partners.
    """

    permission_classes = [CanCommit]

    def get(self, request):
        invoices = (
            Invoice.objects.select_related(
                "organisation", "order", "milestone", "issued_by"
            )
            .prefetch_related("payments")
            .all()
        )

        organisation = request.query_params.get("organisation")
        if organisation:
            invoices = invoices.filter(organisation_id=organisation)

        state = request.query_params.get("status")
        if state in Invoice.Status.values:
            invoices = invoices.filter(status=state)

        return Response({"invoices": InvoiceSerializer(invoices, many=True).data})

    def post(self, request):
        form = DirectInvoiceSerializer(data=request.data)
        form.is_valid(raise_exception=True)

        organisation = get_object_or_404(
            Organisation, pk=form.validated_data["organisation"]
        )
        try:
            invoice = services.issue_direct_invoice(
                organisation=organisation,
                actor=request.user,
                description=form.validated_data["description"],
                amount_kes=form.validated_data["amount_kes"],
                due_on=form.validated_data["due_on"],
                issued_on=form.validated_data["issued_on"],
            )
        except services.OperationsError as exc:
            return _refuse(exc)
        return Response(InvoiceSerializer(invoice).data, status=http.HTTP_201_CREATED)


class AnyInvoicePaymentView(StaffView):
    """
    Add a payment to any invoice, by id.

    Several of these settle one invoice — see PaymentRecord. The arithmetic,
    the overpayment refusal and the duplicate-reference check all live in
    services.record_payment, so this door and the nested one cannot drift.
    """

    permission_classes = [CanCommit]

    def post(self, request, pk: int):
        invoice = get_object_or_404(Invoice, pk=pk)
        form = PaymentSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        try:
            invoice = services.record_payment(
                invoice=invoice,
                actor=request.user,
                amount_kes=form.validated_data["amount_kes"],
                method=form.validated_data["method"],
                reference=form.validated_data["reference"],
                paid_on=form.validated_data["paid_on"],
                note=form.validated_data["note"],
            )
        except services.OperationsError as exc:
            return _refuse(exc)
        return Response(InvoiceSerializer(invoice).data)


class AnyInvoiceVoidView(StaffView):
    """Withdraw any invoice, by id."""

    permission_classes = [CanCommit]

    def post(self, request, pk: int):
        invoice = get_object_or_404(Invoice, pk=pk)
        form = VoidInvoiceSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        try:
            invoice = services.void_invoice(
                invoice=invoice,
                actor=request.user,
                reason=form.validated_data["reason"],
            )
        except services.OperationsError as exc:
            return _refuse(exc)
        return Response(InvoiceSerializer(invoice).data)


class StaffNotificationView(StaffView):
    """
    The signed-in staff member's notifications on the OPERATIONS surface.

    Audience is filtered here rather than accepted from the caller, exactly as
    on the client side. The two surfaces share a table and must not share a
    view of it.
    """

    def get(self, request):
        rows = request.user.notifications.filter(
            audience=Notification.Audience.STAFF
        )[:50]
        unread = request.user.notifications.filter(
            audience=Notification.Audience.STAFF, read_at__isnull=True
        ).count()
        return Response(
            {
                "notifications": NotificationSerializer(rows, many=True).data,
                "unread": unread,
            }
        )

    def post(self, request):
        pk = request.data.get("id")
        rows = request.user.notifications.filter(
            audience=Notification.Audience.STAFF, read_at__isnull=True
        )
        if pk is not None:
            rows = rows.filter(pk=pk)
        rows.update(read_at=timezone.now())
        unread = request.user.notifications.filter(
            audience=Notification.Audience.STAFF, read_at__isnull=True
        ).count()
        return Response({"unread": unread})


class DemandView(StaffView):
    """
    Which services are selling, and which are only being asked about.

    IsStaff, not CanCommit. This is a read of what the company is being asked
    for; anyone doing the asking-and-answering needs to see it, and it exposes
    no client detail — only counts and totals per service.
    """

    def get(self, request):
        return Response({"demand": selectors.demand()})

