"""Dashboard routes. Paths match frontend/src/lib/api.ts."""

from django.urls import path

from . import views

urlpatterns = [
    path("onboarding", views.OnboardingView.as_view(), name="onboarding"),
    # Ordering, for an account that already exists. See EnquiryCreateView —
    # onboarding runs once, so without this a returning client's order was
    # silently discarded.
    path("enquiries", views.EnquiryCreateView.as_view(), name="enquiry-create"),
    path("invoices", views.InvoiceListView.as_view(), name="invoice-list"),
    path("offers", views.OfferListView.as_view(), name="offer-list"),
    path(
        "offers/<str:reference>/decision",
        views.OfferDecisionView.as_view(),
        name="offer-decision",
    ),
    path("notifications", views.NotificationListView.as_view(), name="notifications"),
    path("services", views.ServiceCatalogueView.as_view(), name="service-catalogue"),
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
