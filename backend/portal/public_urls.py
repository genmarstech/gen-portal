"""Unauthenticated routes. See portal/public_api.py for what that means."""

from django.urls import path

from . import public_api

urlpatterns = [
    path("docs", public_api.DocListView.as_view(), name="public-docs"),
    path("docs/<slug:slug>", public_api.DocDetailView.as_view(), name="public-doc"),
    # The work page. Same contract as docs: published rows only, no caller,
    # nothing that identifies a member of staff.
    path("work", public_api.WorkListView.as_view(), name="public-work"),
    # Products are the same table filtered by label — see WorkPublished. Two
    # routes because they are two pages answering two different questions, not
    # because they are two kinds of record.
    path("products", public_api.ProductListView.as_view(), name="public-products"),
]
