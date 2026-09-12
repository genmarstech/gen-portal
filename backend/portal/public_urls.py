"""Unauthenticated routes. See portal/public_api.py for what that means."""

from django.urls import path

from . import public_api

urlpatterns = [
    path("docs", public_api.DocListView.as_view(), name="public-docs"),
    path("docs/<slug:slug>", public_api.DocDetailView.as_view(), name="public-doc"),
]
