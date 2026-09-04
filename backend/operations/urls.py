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
    # One box over everything — see operations/search.py for the ranking.
    path("ops/search", views.SearchView.as_view(), name="ops-search"),
    path("ops/staff", views.StaffDirectoryView.as_view(), name="ops-staff"),
    path("ops/staff/<int:pk>", views.StaffDetailView.as_view(), name="ops-staff-member"),

    # invoicing — flat, so every invoice is reachable whether or not it has
    # an order behind it. The nested routes below stay for milestone billing.
    path("ops/invoices", views.AllInvoiceListView.as_view(), name="ops-all-invoices"),
    path(
        "ops/invoices/<int:pk>/payments",
        views.AnyInvoicePaymentView.as_view(),
        name="ops-invoice-payments",
    ),
    path(
        "ops/invoices/<int:pk>/void",
        views.AnyInvoiceVoidView.as_view(),
        name="ops-invoice-void-flat",
    ),
    # The company's own billing identity. Read by any staff account, written
    # only by a founder — see BillingProfileView.
    path(
        "ops/billing-profile",
        views.BillingProfileView.as_view(),
        name="ops-billing-profile",
    ),

    path(
        "ops/notifications",
        views.StaffNotificationView.as_view(),
        name="ops-notifications",
    ),

    # invoicing
    path(
        "ops/orders/<str:reference>/invoices",
        views.InvoiceListView.as_view(),
        name="ops-invoices",
    ),
    path(
        "ops/orders/<str:reference>/invoices/<int:pk>/payment",
        views.InvoicePaymentView.as_view(),
        name="ops-invoice-payment",
    ),
    path(
        "ops/orders/<str:reference>/invoices/<int:pk>/void",
        views.InvoiceVoidView.as_view(),
        name="ops-invoice-void",
    ),

    path("ops/demand", views.DemandView.as_view(), name="ops-demand"),

    # ---- the workroom: who is here, and what we decided ----
    path("ops/clock", views.ClockView.as_view(), name="ops-clock"),
    path("ops/timesheet", views.TimesheetView.as_view(), name="ops-timesheet"),
    path("ops/decisions", views.DecisionListView.as_view(), name="ops-decisions"),
    path("ops/decisions/<int:pk>", views.DecisionDetailView.as_view(), name="ops-decision"),

    # ---- catalogue pricing and the log ----
    path("ops/tiers", views.TierListView.as_view(), name="ops-tiers"),
    path("ops/tiers/<int:pk>/price", views.TierPriceView.as_view(), name="ops-tier-price"),
    path("ops/activity", views.ActivityView.as_view(), name="ops-activity"),

    # ---- offers and internal work ----
    path("ops/offers", views.OfferListView.as_view(), name="ops-offers"),
    path("ops/offers/<int:pk>/action", views.OfferActionView.as_view(), name="ops-offer-action"),
    path("ops/tasks", views.TaskListView.as_view(), name="ops-tasks"),
    path("ops/tasks/<int:pk>", views.TaskDetailView.as_view(), name="ops-task"),

    # ---- the systems this portal is parent to ----
    path("ops/systems", views.SystemListView.as_view(), name="ops-systems"),
    path("ops/systems/events", views.SystemEventFeedView.as_view(), name="ops-system-events"),
    path("ops/systems/<slug:slug>", views.SystemDetailView.as_view(), name="ops-system"),
    path("ops/systems/<slug:slug>/keys", views.SystemKeyView.as_view(), name="ops-system-keys"),
    path(
        "ops/systems/<slug:slug>/security/<int:pk>",
        views.SecurityCheckView.as_view(),
        name="ops-security-check",
    ),

    # ---- support ----
    path("ops/support", views.TicketListView.as_view(), name="ops-tickets"),
    path("ops/support/<str:reference>/reply", views.TicketReplyView.as_view(), name="ops-ticket-reply"),
    path("ops/support/<str:reference>", views.TicketStateView.as_view(), name="ops-ticket"),

    # ---- incidents ----
    path("ops/incidents", views.IncidentListView.as_view(), name="ops-incidents"),
    path("ops/incidents/<int:pk>", views.IncidentDetailView.as_view(), name="ops-incident"),
    path("ops/incidents/<int:pk>/status", views.IncidentStatusView.as_view(), name="ops-incident-status"),

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

    # ---- client accounts ----
    path("ops/organisations", views.OrganisationListView.as_view(), name="ops-organisations"),
    # The client record: who they are, what we run for them, what was said.
    path("ops/clients/<int:pk>", views.ClientRecordView.as_view(), name="ops-client"),
    # Rename, archive, restore, delete. Permission differs per verb — see
    # OrganisationListView for the split and why.
    path("ops/clients/<int:pk>/admin", views.ClientAdminView.as_view(), name="ops-client-admin"),
    path("ops/clients/<int:pk>/hosting", views.ClientHostingView.as_view(), name="ops-client-hosting"),
    path("ops/clients/<int:pk>/contact", views.ContactLogView.as_view(), name="ops-client-contact"),
    path("ops/hosting/<int:pk>", views.HostingDetailView.as_view(), name="ops-hosting"),
    path(
        "ops/contact/<int:pk>/attachments",
        views.ContactAttachmentView.as_view(),
        name="ops-contact-attachments",
    ),
    path(
        "ops/attachments/<int:pk>",
        views.ContactAttachmentView.as_view(),
        name="ops-attachment",
    ),
    path("ops/clients/<int:pk>/orders", views.ClientOrderView.as_view(), name="ops-client-orders"),
    path("ops/follow-ups", views.FollowUpView.as_view(), name="ops-follow-ups"),
    # Recent conversations across every client, for assigning work off one.
    path(
        "ops/conversations",
        views.ConversationPickerView.as_view(),
        name="ops-conversations",
    ),

    # ---- asking a founder for permission to do one thing, once ----
    path("ops/requests", views.AccessRequestView.as_view(), name="ops-requests"),
    path(
        "ops/requests/<int:pk>",
        views.AccessRequestDecisionView.as_view(),
        name="ops-request",
    ),
    path("ops/organisations/<int:pk>/members", views.OrganisationMembersView.as_view(), name="ops-org-members"),
    path("ops/memberships/<int:pk>", views.MembershipDetailView.as_view(), name="ops-membership"),

    # ---- services and contracts ----
    path("ops/services", views.ServiceListView.as_view(), name="ops-services"),
    path("ops/services/<int:pk>", views.ServiceDetailView.as_view(), name="ops-service"),
    path("ops/orders/<str:reference>/contracts", views.ContractListView.as_view(), name="ops-contracts"),
    path("ops/orders/<str:reference>/contracts/<int:pk>/sign", views.ContractSignView.as_view(), name="ops-contract-sign"),
    path("ops/orders/<str:reference>/contracts/<int:pk>/void", views.ContractVoidView.as_view(), name="ops-contract-void"),
]
