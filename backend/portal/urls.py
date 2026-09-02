"""Dashboard routes. Paths match frontend/src/lib/api.ts."""

from django.urls import path

from . import views

urlpatterns = [
    path("onboarding", views.OnboardingView.as_view(), name="onboarding"),
    path("orders", views.OrderListView.as_view(), name="order-list"),
    path("orders/<str:reference>", views.OrderDetailView.as_view(), name="order-detail"),
    path(
        "orders/<str:reference>/invoices/<str:number>",
        views.InvoiceDocumentView.as_view(),
        name="invoice-document",
    ),
    path("account/export", views.ExportView.as_view(), name="export"),
]
