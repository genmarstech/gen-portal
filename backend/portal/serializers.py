"""
Serializers for the client-facing dashboard.

All read-only. Nothing a client sends can create or change an order — orders
come from a signed SOW, not a form (Charter 02 §I).

What is exposed is decided by Charter 05, the Client Charter: the promise names
scope, exclusions, a weekly progress note, milestones, and a named contact. The
dashboard shows exactly that and no internal state beyond it.
"""

from __future__ import annotations

from rest_framework import serializers

from .models import Contract, Invoice, Milestone, Order, ProgressNote


class ContactSerializer(serializers.Serializer):
    """
    Charter 05 §I — "a named point of contact".

    Name and email only. A staff member's account flags, last login and internal
    identifiers are none of the client's business, and a ModelSerializer over
    User would leak them the moment someone adds a field.
    """

    full_name = serializers.CharField()
    email = serializers.EmailField()


class ProgressNoteSerializer(serializers.ModelSerializer):
    author = serializers.CharField(source="author.full_name", read_only=True)

    class Meta:
        model = ProgressNote
        fields = ["week_of", "body", "author", "published_at"]
        read_only_fields = fields


class MilestoneSerializer(serializers.ModelSerializer):
    status_label = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = Milestone
        # amount_kes is a Decimal and is serialised as a STRING, which is DRF's
        # default and the right one: JSON numbers are floats, and money that has
        # been through a float is money you cannot reconcile.
        fields = ["name", "amount_kes", "due_on", "status", "status_label"]
        read_only_fields = fields


class OrderListSerializer(serializers.ModelSerializer):
    status_label = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = Order
        fields = ["reference", "title", "status", "status_label", "target_date"]
        read_only_fields = fields


class ClientContractSerializer(serializers.ModelSerializer):
    """
    The statement of work, as the CLIENT sees it.

    Deliberately narrower than the operations serializer. Omitted:

      · `status` — the client only ever receives a live version, so the field
        would carry no information and "issued" vs "signed" is answered by
        whether signed_on is set.
      · `recorded_by` and `signature_note` — who at Genmars wrote the signature
        down, and where the evidence is filed, are our bookkeeping. The client
        knows they signed; being shown our internal note about it is odd at
        best.
      · every earlier version — a superseded contract is not what is in force,
        and showing the client three of them invites arguing from the wrong one.

    What IS here is the whole of what was agreed: scope, exclusions,
    deliverables, price and terms. Charter 05 §I — in writing, and the client
    can read it back at any time.
    """

    reference = serializers.CharField(read_only=True)
    deliverable_list = serializers.ListField(child=serializers.CharField(), read_only=True)
    # A STRING. This is the number on a document they signed.
    total_kes = serializers.DecimalField(
        max_digits=12, decimal_places=2, coerce_to_string=True, read_only=True
    )

    class Meta:
        model = Contract
        fields = [
            "reference", "version", "title", "scope", "exclusions",
            "deliverables", "deliverable_list", "total_kes", "payment_terms",
            "target_date", "issued_at", "signed_on", "signed_by_name",
        ]
        read_only_fields = fields


class ClientInvoiceSerializer(serializers.ModelSerializer):
    """
    An invoice, as the CLIENT sees it.

    ── VOIDED INVOICES ARE SHOWN, NOT HIDDEN ───────────────────────────────────

    The tempting behaviour is to filter them out — they are not owed, so why
    clutter the page. But a voided invoice is one we already SENT. It is sitting
    in their inbox and possibly in their accounts system. Hiding it here means
    they hold a document the portal says does not exist, and the only way they
    find out it was withdrawn is by paying it.

    So it is listed, marked void, with the reason. That reason is written for
    them to read (services.void_invoice requires one), which is the point.

    ── WHAT IS OMITTED ─────────────────────────────────────────────────────────

    `issued_by` — which of the three of us pressed the button is our
    bookkeeping, not a fact about their bill.
    """

    status_label = serializers.CharField(source="get_status_display", read_only=True)
    # A STRING. This is the number they are being asked to pay.
    amount_kes = serializers.DecimalField(
        max_digits=12, decimal_places=2, coerce_to_string=True, read_only=True
    )
    overdue = serializers.SerializerMethodField()

    class Meta:
        model = Invoice
        fields = [
            "number", "description", "amount_kes", "status", "status_label",
            "issued_on", "due_on", "overdue", "paid_on",
            # The reference we matched their payment against. Shown so they can
            # confirm we credited the payment they actually made, rather than
            # taking "paid" on trust.
            "payment_reference",
            "void_reason",
        ]
        read_only_fields = fields

    def get_overdue(self, invoice: Invoice) -> bool:
        return invoice.is_overdue()


class OrderDetailSerializer(serializers.ModelSerializer):
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    contact = ContactSerializer(read_only=True)
    organisation = serializers.CharField(source="organisation.name", read_only=True)
    notes = serializers.SerializerMethodField()
    milestones = MilestoneSerializer(many=True, read_only=True)
    contract = serializers.SerializerMethodField()
    invoices = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = [
            "reference",
            "title",
            "organisation",
            "scope",
            # Exclusions are shown as prominently as scope. Charter 05 §I puts
            # them in the promise for a reason: "anything not in the written
            # scope is a change request", and a client can only hold us to that
            # if they can see what was excluded.
            "exclusions",
            "status",
            "status_label",
            "contact",
            "started_on",
            "target_date",
            "notes",
            "milestones",
            "contract",
            "invoices",
        ]
        read_only_fields = fields

    def get_contract(self, order: Order):
        """
        The statement of work in force, or null.

        Null is an ordinary state, not an error: an order in scoping has not
        been contracted yet, and Charter 02 §I is explicit that work begins
        when a SOW is signed rather than when an order row exists. The client
        seeing "no contract yet" on an order at scoping is the truth.

        Scoped in selectors.py, like the notes, so "issued or signed only" is
        stated once.
        """
        from .selectors import live_contract_for

        contract = live_contract_for(order)
        return ClientContractSerializer(contract).data if contract else None

    def get_invoices(self, order: Order):
        """
        Everything ever billed on this order, newest first — voids included.
        See ClientInvoiceSerializer for why.
        """
        invoices = order.invoices.all()
        return ClientInvoiceSerializer(invoices, many=True).data

    def get_notes(self, order: Order):
        """
        Published notes only, newest first.

        A draft is not a promise — the client is entitled to rely on what they
        were actually told, and nothing else. Scoped in selectors.py so the rule
        is not restated per view.
        """
        from .selectors import published_notes_for

        return ProgressNoteSerializer(published_notes_for(order), many=True).data


class OnboardingSerializer(serializers.Serializer):
    """
    What onboarding accepts. Note what it does NOT accept.

    No status, no order, no reference, no amount — nothing that would let a
    client create or influence an engagement. Charter 02 §I gives qualification
    to the commercial partners and the capacity veto to the founder; a form that
    could produce an Order would route around both. This produces an Enquiry,
    and an Enquiry is a request to be considered.
    """

    full_name = serializers.CharField(max_length=200)
    organisation_name = serializers.CharField(max_length=200)

    # The Playbook's qualification questions. Only the first is required: a
    # prospect who does not yet know their budget still has a real problem, and
    # making them invent a number to get past a form teaches them to lie to us.
    problem = serializers.CharField()
    monthly_cost = serializers.CharField(max_length=200, allow_blank=True, default="")
    timeline = serializers.CharField(max_length=100, allow_blank=True, default="")
    budget_range = serializers.CharField(max_length=100, allow_blank=True, default="")

    # What they clicked on genmars.co.ke, when they came from a specific
    # offering rather than the open route. Both optional — see Enquiry.service.
    #
    # A SLUG, not a primary key. The website is a static export that knows
    # nothing about this database; a pk would couple the two and break the
    # moment a service is reseeded. An unrecognised slug is dropped rather than
    # rejected: the visitor did nothing wrong, and losing the attribution is
    # much better than refusing the enquiry.
    service = serializers.CharField(max_length=120, required=False, allow_blank=True, default="")
    tier = serializers.CharField(max_length=120, required=False, allow_blank=True, default="")

    def validate_organisation_name(self, value: str) -> str:
        name = value.strip()
        if not name:
            raise serializers.ValidationError("Please give your organisation a name.")
        return name

    def validate_problem(self, value: str) -> str:
        problem = value.strip()
        # Not a length gate for its own sake: this text is what the commercial
        # partners qualify against, and "hi" gives them nothing to work with.
        if len(problem) < 20:
            raise serializers.ValidationError(
                "Tell us a little more — a sentence or two about what is going wrong."
            )
        return problem


class InvoiceDocumentSerializer(serializers.Serializer):
    """
    An invoice as a DOCUMENT — everything needed to render something a client
    can print, file, and pay against.

    ── WHY THIS IS NOT JUST ClientInvoiceSerializer WITH MORE FIELDS ───────────

    A row in a list answers "what do I owe". A document answers "who is billing
    me, for what, under what agreement, and how do I pay". The second needs the
    biller's identity, the client's own name for their records, the order and
    contract it arises from, and payment instructions — none of which belong in
    a list.

    ── EMPTY BILLING FIELDS ARE OMITTED, NEVER GUESSED ────────────────────────

    See config/settings.py. An invoice carrying an invented KRA PIN or paybill
    is not a cosmetic bug — it is a document somebody pays against, or fails to
    file. Anything unconfigured is simply absent, and the page says plainly that
    payment details will come from the named contact.
    """

    invoice = ClientInvoiceSerializer(read_only=True)
    billed_to = serializers.SerializerMethodField()
    biller = serializers.SerializerMethodField()
    payment = serializers.SerializerMethodField()
    order = serializers.SerializerMethodField()

    def get_billed_to(self, data) -> dict:
        """
        The organisation, and nothing else.

        `order.contact` is the GENMARS named contact (Charter 05 §I — our point
        of contact for them), and putting it here made the document read
        "To: Kilimani Dental, Edwin" — as though we were billing our own staff.

        The temptation is to substitute the client's account owner instead.
        That is also wrong: whoever signed up is not necessarily whoever pays,
        and addressing an invoice to the wrong named person inside a company is
        how it sits unpaid in somebody's inbox. An invoice is addressed to the
        organisation, which is both correct and what their accounts department
        expects.
        """
        return {"organisation": data["order"].organisation.name, "contact": ""}

    def get_biller(self, data) -> dict:
        from django.conf import settings

        # Only what is configured. `or None` rather than "" so the client can
        # test truthiness without caring which fields exist.
        return {
            "legal_name": settings.BILLING_LEGAL_NAME,
            "email": settings.BILLING_EMAIL,
            "kra_pin": settings.BILLING_KRA_PIN or None,
            "postal_address": settings.BILLING_POSTAL_ADDRESS or None,
        }

    def get_payment(self, data) -> dict:
        from django.conf import settings

        invoice = data["invoice"]
        paybill = settings.BILLING_MPESA_PAYBILL or None
        account = None
        if paybill:
            # The invoice number in the account field is what makes a payment
            # reconcilable without a phone call.
            account = settings.BILLING_MPESA_ACCOUNT_HINT.replace(
                "{number}", invoice.number
            )

        return {
            "mpesa_paybill": paybill,
            "mpesa_account": account,
            "bank_details": settings.BILLING_BANK_DETAILS or None,
            "terms": settings.BILLING_TERMS,
            # False today. When M-Pesa credentials are configured this becomes
            # true and the client may pay from the page. Until then NOTHING in
            # the UI may suggest the capability exists — a button that only
            # marked a row would leave the client believing they had paid.
            "stk_available": settings.MPESA_ENABLED,
        }

    def get_order(self, data) -> dict:
        order = data["order"]
        contract = data["contract"]
        return {
            "reference": order.reference,
            "title": order.title,
            # The agreement this bill arises from. A client asking "what is this
            # for" should not have to go and look for it.
            "contract_reference": contract.reference if contract else None,
            "contract_signed_on": (
                contract.signed_on.isoformat()
                if contract and contract.signed_on
                else None
            ),
        }
