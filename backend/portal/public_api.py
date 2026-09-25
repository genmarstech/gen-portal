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

from django.conf import settings
from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Doc, WorkItem


class PublicRead(APIView):
    """
    A public, unauthenticated, cross-origin-readable GET. Nothing else.

    ══════════════════════════════════════════════════════════════════════════
    THIS IS THE ONLY PLACE IN THIS DJANGO THAT SENDS Access-Control-Allow-Origin.

    settings.py says the portal needs no CORS anywhere, and that is still true:
    the browser only ever talks to app.genmars.co.ke, which proxies /api/*
    server-side, so those calls are same-origin. This is the one exception, and
    it exists because genmars.co.ke now refreshes /work in the visitor's browser
    so publishing shows up without a deploy.

    Three properties make that safe, and removing any one of them breaks it:

      1. `authentication_classes = []` — no session, token or key is read here,
         so there is no credential for a cross-origin caller to borrow. This is
         the load-bearing one.
      2. NO Access-Control-Allow-Credentials, ever. Setting it is what turns a
         readable endpoint into one that acts as somebody else. There is no
         session to act as today; the header must not be here waiting for the
         day there is.
      3. Reads only. The preflight advertises GET, HEAD and OPTIONS, and the
         views define no other verb.

    ⚠ DO NOT PUT THIS MIXIN ON ANYTHING UNDER /api/ OR /api/ops/. Those are
      scoped to a caller, so they DO read a credential, and property 1 is
      immediately false. This belongs to /api/public/ alone — which is why it
      lives in this file and not in a shared module where it would look
      generally applicable.

    An allowlist rather than `*`, not because `*` would leak anything here, but
    because the header then names who this was opened for and a reviewer can
    tell whether that is still the intent.
    ══════════════════════════════════════════════════════════════════════════
    """

    permission_classes = [AllowAny]
    authentication_classes = []

    def _allow(self, request, response):
        origin = request.headers.get("Origin")
        if origin and origin in settings.PUBLIC_API_CORS_ORIGINS:
            response["Access-Control-Allow-Origin"] = origin
        # Always, even when the origin did not match: the response body varies
        # by Origin, and a cache that does not know that will serve the
        # allowed origin's headers to everybody else, or the reverse.
        response["Vary"] = (
            f"{response['Vary']}, Origin" if response.has_header("Vary") else "Origin"
        )
        return response

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        return self._allow(request, response)

    def options(self, request, *args, **kwargs):
        """The preflight. Reads only, and deliberately no credentials header."""
        response = super().options(request, *args, **kwargs)
        response["Access-Control-Allow-Methods"] = "GET, HEAD, OPTIONS"
        response["Access-Control-Allow-Headers"] = "Accept, Content-Type"
        response["Access-Control-Max-Age"] = "86400"
        return response


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
        """Everything publishable, products and work alike."""
        from django.db.models import Q

        return WorkItem.objects.filter(
            Q(is_published=True)
            & (
                Q(permission_on_file=True)
                | ~Q(label__in=list(WorkItem.NEEDS_CONSENT))
            )
        )

    # ── PRODUCTS AND WORK ARE ONE TABLE, SPLIT BY LABEL ─────────────────────
    #
    # `label` already drew this line before anybody asked for two pages:
    # PRODUCT is something Genmars owns and sells; INTERNAL, CLIENT, CONCEPT
    # and RESEARCH are things we did. So the split is a filter, not a second
    # model — one editing surface in ops, one consent gate, and no chance of
    # an item existing in one place and not the other.
    #
    # ⚠ THE TWO ARE COMPLEMENTS AND MUST STAY SO. Anything publishable appears
    #   on exactly one of the two pages. A third label added to `Label` lands
    #   in work by default, which is the safe side: a new kind of thing showing
    #   up beside our own products would overclaim, and showing up beside our
    #   work would not.

    @staticmethod
    def products():
        return WorkPublished.all().filter(label=WorkItem.Label.PRODUCT)

    @staticmethod
    def work():
        return WorkPublished.all().exclude(label=WorkItem.Label.PRODUCT)


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
            # The picture and the credit it obliges us to print. Sent together
            # and always: the site cannot render one without the other, and a
            # payload that carried the URL alone would make an attribution
            # breach a front-end bug rather than an impossible state.
            "image_url",
            "image_alt",
            "image_credit_name",
            "image_credit_url",
            "order",
            "updated_at",
        ]


def _grouped(items) -> dict:
    """The payload both public lists answer with."""
    return {
        "work": WorkItemSerializer(items, many=True).data,
        # Only the categories with something in them, in the model's declared
        # order. A heading with nothing under it is a gap the reader assumes
        # is a bug — same rule as /docs.
        "categories": [
            {"key": key, "label": label}
            for key, label in WorkItem.Category.choices
            if any(i.category == key for i in items)
        ],
    }


class WorkListView(PublicRead):
    """
    What we have been doing — and NOT what we sell.

    Products moved to their own page and their own endpoint, so this is
    concepts, research, internal systems and client work. The key is still
    called "work" because the website reads both lists with the same code and
    renaming it would be a breaking change for a cosmetic gain.
    """

    def get(self, request):
        items = WorkPublished.work().order_by("category", "order", "-year", "name")
        return Response(_grouped(items))


class ProductListView(PublicRead):
    """
    What Genmars owns and sells.

    A separate page because the two answer different questions. "Can you build
    something like this" is asked of a portfolio; "can I buy this" is asked of
    a product, and a visitor who wants the second should not have to infer it
    from a list of research projects.

    Same serializer and same shape as work, deliberately: one payload type,
    one renderer on the site, and no second thing to keep in step.
    """

    def get(self, request):
        items = WorkPublished.products().order_by("order", "-year", "name")
        return Response(_grouped(items))


class DocListView(PublicRead):
    """Every published document, with its body — the build fetches once."""


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


class DocDetailView(PublicRead):
    """One document. Not used by the build; here for anyone reading the API."""


    def get(self, request, slug: str):
        # 404 for a draft, not 403 — the same rule as everywhere else in this
        # project. A 403 would confirm the slug exists and is being worked on.
        doc = get_object_or_404(published(), slug=slug)
        return Response(DocSerializer(doc).data)
