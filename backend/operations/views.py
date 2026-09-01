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
from rest_framework import status as http
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import User
from portal.models import Milestone, Order, ProgressNote

from . import selectors, services
from .permissions import IsStaff
from .serializers import (
    ConvertSerializer,
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

        try:
            order = services.convert_enquiry(
                enquiry=enquiry,
                actor=request.user,
                title=data.validated_data["title"],
                scope=data.validated_data["scope"],
                exclusions=data.validated_data["exclusions"],
                contact=contact,
                target_date=data.validated_data.get("target_date"),
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
    """Genmars accounts, for the 'named contact' picker. No client accounts."""

    def get(self, request):
        people = User.objects.filter(is_staff=True).order_by("full_name", "email")
        return Response(
            {
                "staff": [
                    {"id": u.pk, "full_name": u.full_name, "email": u.email}
                    for u in people
                ]
            }
        )
