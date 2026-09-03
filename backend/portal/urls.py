"""Dashboard routes. Paths match frontend/src/lib/api.ts."""

from django.urls import path

from . import system_api, views

urlpatterns = [
    path("onboarding", views.OnboardingView.as_view(), name="onboarding"),
    # Ordering, for an account that already exists. See EnquiryCreateView —
    # onboarding runs once, so without this a returning client's order was
    # silently discarded.
    path("enquiries", views.EnquiryCreateView.as_view(), name="enquiry-create"),
    path("dashboard", views.DashboardView.as_view(), name="dashboard"),
    path("invoices", views.InvoiceListView.as_view(), name="invoice-list"),
    # ── one invoice, by number ──────────────────────────────────────────────
    #
    # The canonical route, and flat on purpose. Not every invoice has an order
    # behind it — a renewal, an afternoon's work, something billed to a past
    # client — and those cannot be addressed under /orders/<reference>/ at all.
    # Nesting them there was why a client could be sent a bill their own portal
    # insisted did not exist.
    #
    # The nested routes below are kept and still strict; see _resolve_invoice.
    path(
        "invoices/<str:number>",
        views.InvoiceDocumentView.as_view(),
        name="invoice-document-flat",
    ),
    path(
        "invoices/<str:number>/pay",
        views.InvoicePayView.as_view(),
        name="invoice-pay-flat",
    ),
    path(
        "invoices/<str:number>/payment-status",
        views.InvoicePaymentStatusView.as_view(),
        name="invoice-payment-status-flat",
    ),
    path("support", views.SupportView.as_view(), name="support"),
    path(
        "support/<str:reference>/reply",
        views.SupportReplyView.as_view(),
        name="support-reply",
    ),
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
    # ── machine-facing, token-authenticated. See portal/system_api.py ──
    path(
        "systems/heartbeat",
        system_api.HeartbeatView.as_view(),
        name="system-heartbeat",
    ),
    path("systems/events", system_api.SystemEventView.as_view(), name="system-event"),

    path("mpesa/callback", views.MpesaCallbackView.as_view(), name="mpesa-callback"),
    path(
        "mpesa/callback/<str:token>",
        views.MpesaCallbackView.as_view(),
        name="mpesa-callback-token",
    ),
    path("account/export", views.ExportView.as_view(), name="export"),
]
