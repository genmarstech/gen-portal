"""
The documentation the marketing site builds from.

═══════════════════════════════════════════════════════════════════════════════
EVERY VIEW IN THIS FILE IS UNAUTHENTICATED. THAT IS THE POINT OF THE FILE.

Nothing else in the portal answers without a session. These do, so "who can
read this" is a property of the whole module rather than of a decorator
somebody has to notice — the same reason tenant isolation lives in exactly one
file. If a view needs a caller, it does not belong here.

Three rules, and they are the whole security model:

  · PUBLISHED ROWS ONLY. The queryset filters on is_published and there is no
    parameter that widens it. A draft is invisible, not merely unlisted.
  · TWO MODELS ARE REACHED, Doc AND WorkItem, AND NO OTHERS. No joins, no
    related fields, no counts over other tables. WorkItem was the second and
    had to make the argument again from scratch: it is marketing copy about
    our own work, written by staff for strangers to read, which is exactly
    what Doc is. A model that holds anything a client told us does not
    qualify, however convenient the route would be.
  · NOTHING IDENTIFIES ANYBODY. Not the author, not who last edited it, not
    when staff were working. `updated_by` exists on the model and is not
    serialised, deliberately.
═══════════════════════════════════════════════════════════════════════════════

── WHY THE MARKETING SITE READS THIS AT BUILD TIME AND NOT AT RUNTIME ──────────

genmars.co.ke is a static export with no server. Its build calls this once and
writes real HTML; a visitor's browser never contacts this API at all. That
keeps three things true which a runtime fetch would each cost: /docs does not
break when the API is down, the public origin does not need connect-src opened
to api.genmars.co.ke, and a crawler sees the words without executing anything.

The price is that publishing takes a deploy. See Doc's docstring.

── WHY NOT UNDER /api/ WITH THE CLIENT ROUTES ──────────────────────────────────

Because /api/ means "scoped to the caller's organisations" everywhere else in
this project, and this is the opposite of scoped. One segment of difference in
a proxy rule or a log line is the whole distinction between the two, exactly as
/api/ and /api/ops/ are held apart. A public route under /api/public/ cannot be
mistaken for a client one at a glance.
"""

from __future__ import annotations

from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Doc, WorkItem


def published() -> "models.QuerySet[Doc]":  # noqa: F821 - annotation only
    """
    The one queryset in this module.

    Written as a function so there is a single place to read, rather than
    `Doc.objects.filter(is_published=True)` repeated per view — which is how
    one view eventually gets written without the filter.
    """
    return Doc.objects.filter(is_published=True)


class DocSerializer(serializers.ModelSerializer):
    """
    One document, with its body.

    `updated_at` is included because documentation with no date is
    documentation nobody trusts. `updated_by` is not: which member of the team
    last touched a page is nobody else's business.

    ── status_changed_at TRAVELS WITH status_note, ALWAYS ──────────────────────

    A progress note is only honest next to the date it was written. Serialising
    the note without the date would let the site render "reports and inventory
    are working, permissions next" as though somebody checked this morning.
    They are one fact in two fields; if one is ever dropped from this list, the
    other goes with it.
    """

    class Meta:
        model = Doc
        fields = [
            "slug",
            "title",
            "summary",
            "category",
            "body",
            "repo_url",
            "order",
            "status",
            "status_note",
            "status_changed_at",
            "updated_at",
        ]


class WorkPublished:
    """
    The one queryset for work, and the consent gate lives inside it.

    ══════════════════════════════════════════════════════════════════════════
    THE GATE IS HERE, NOT IN THE EDITOR AND NOT ON THE WEBSITE.

    Charter 04 §V — credited only with written permission. An item labelled a
    client system is invisible until `permission_on_file` is set, and that is
    enforced by the queryset that answers the internet rather than by an
    editor remembering to leave a box unticked.

    Something Genmars owns and runs has nobody to ask, so it needs no flag.
    That distinction is the thing the website's old all-or-nothing gate got
    wrong: it hid our own product behind two unrelated clients' signatures.
    ══════════════════════════════════════════════════════════════════════════
    """

    @staticmethod
    def all():
        from django.db.models import Q

        return WorkItem.objects.filter(
            Q(is_published=True)
            & (
                Q(permission_on_file=True)
                | ~Q(label__in=list(WorkItem.NEEDS_CONSENT))
            )
        )


class WorkItemSerializer(serializers.ModelSerializer):
    """
    One piece of work.

    `capabilities` is sent as a list rather than the newline blob it is
    stored as, so the site is not parsing a textarea. `updated_by` is absent
    for the same reason it is absent from a Doc: which member of the team
    wrote it is nobody else's business.
    """

    capabilities = serializers.ListField(
        source="capability_list", child=serializers.CharField(), read_only=True
    )
    label_display = serializers.CharField(source="get_label_display", read_only=True)
    category_display = serializers.CharField(
        source="get_category_display", read_only=True
    )

    class Meta:
        model = WorkItem
        fields = [
            "slug",
            "name",
            "category",
            "category_display",
            "label",
            "label_display",
            "sector",
            "year",
            "url",
            "summary",
            "detail",
            "capabilities",
            "architecture",
            "engineering",
            "results",
            "order",
            "updated_at",
        ]


class WorkListView(APIView):
    """Everything publishable, grouped the way the site groups it."""

    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        items = WorkPublished.all().order_by("category", "order", "-year", "name")
        return Response(
            {
                "work": WorkItemSerializer(items, many=True).data,
                # Only the categories with something in them, in the model's
                # declared order. A heading with nothing under it is a gap the
                # reader assumes is a bug — same rule as /docs.
                "categories": [
                    {"key": key, "label": label}
                    for key, label in WorkItem.Category.choices
                    if any(i.category == key for i in items)
                ],
            }
        )


class DocListView(APIView):
    """Every published document, with its body — the build fetches once."""

    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        docs = published().order_by("category", "order", "title")
        return Response(
            {
                "docs": DocSerializer(docs, many=True).data,
                # The categories that actually have something in them, in the
                # model's declared order. A heading with nothing under it is a
                # gap the reader assumes is a bug.
                "categories": [
                    {"key": key, "label": label}
                    for key, label in Doc.Category.choices
                    if any(d.category == key for d in docs)
                ],
                # The labels the site prints on a status badge. Sent rather
                # than duplicated in the frontend, so /docs and /services
                # cannot end up calling the same state different things.
                "statuses": [
                    {"key": key, "label": label} for key, label in Doc.Status.choices
                ],
            }
        )


class DocDetailView(APIView):
    """One document. Not used by the build; here for anyone reading the API."""

    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request, slug: str):
        # 404 for a draft, not 403 — the same rule as everywhere else in this
        # project. A 403 would confirm the slug exists and is being worked on.
        doc = get_object_or_404(published(), slug=slug)
        return Response(DocSerializer(doc).data)
