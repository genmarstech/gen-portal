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
    path(
        "orders/<str:reference>/invoices/<str:number>/pay",
        views.InvoicePayView.as_view(),
        name="invoice-pay",
    ),
    path(
        "orders/<str:reference>/invoices/<str:number>/payment-status",
        views.InvoicePaymentStatusView.as_view(),
        name="invoice-payment-status",
    ),
    # Safaricom posts here. The token is part of the PATH because Daraja sends
    # no headers of ours — see MpesaCallbackView. Both spellings are routed so
    # a deployment that has not set a token still works.
    path("mpesa/callback", views.MpesaCallbackView.as_view(), name="mpesa-callback"),
    path(
        "mpesa/callback/<str:token>",
        views.MpesaCallbackView.as_view(),
        name="mpesa-callback-token",
    ),
    path("account/export", views.ExportView.as_view(), name="export"),
]
