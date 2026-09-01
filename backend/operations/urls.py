"""
Operations routes. Mounted at /api/ops/ — see config/urls.py.

Namespaced away from the client API on purpose: /api/orders is what a client
sees, /api/ops/orders is every order in the company. Two paths that differ by
one segment and by the whole of Charter 05 §V, so they are never confused for
each other in a proxy rule or a log line.
"""

from django.urls import path

from . import views

urlpatterns = [
    path("ops/overview", views.OverviewView.as_view(), name="ops-overview"),
    path("ops/staff", views.StaffDirectoryView.as_view(), name="ops-staff"),

    path("ops/enquiries", views.EnquiryListView.as_view(), name="ops-enquiries"),
    path("ops/enquiries/<int:pk>", views.EnquiryDetailView.as_view(), name="ops-enquiry"),
    path("ops/enquiries/<int:pk>/convert", views.EnquiryConvertView.as_view(), name="ops-convert"),

    path("ops/orders", views.OrderListView.as_view(), name="ops-orders"),
    path("ops/orders/<str:reference>", views.OrderDetailView.as_view(), name="ops-order"),
    path("ops/orders/<str:reference>/notes", views.OrderNoteView.as_view(), name="ops-order-notes"),
    path("ops/orders/<str:reference>/notes/<int:pk>/publish", views.NotePublishView.as_view(), name="ops-note-publish"),
    path("ops/orders/<str:reference>/milestones", views.OrderMilestoneView.as_view(), name="ops-order-milestones"),
    path("ops/orders/<str:reference>/milestones/<int:pk>", views.MilestoneDetailView.as_view(), name="ops-milestone"),

    # ---- engineering delivery ----
    path("ops/delivery", views.DeliveryBoardView.as_view(), name="ops-delivery"),
    path("ops/delivery/backfill", views.BackfillGatesView.as_view(), name="ops-delivery-backfill"),
    path("ops/orders/<str:reference>/gates/<int:pk>", views.GateView.as_view(), name="ops-gate"),
    path("ops/orders/<str:reference>/blockers", views.BlockerListView.as_view(), name="ops-blockers"),
    path("ops/orders/<str:reference>/blockers/<int:pk>", views.BlockerDetailView.as_view(), name="ops-blocker"),
]
