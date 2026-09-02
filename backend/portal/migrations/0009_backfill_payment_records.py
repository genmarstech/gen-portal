"""
Give every already-paid invoice the payment row it should have had.

═══════════════════════════════════════════════════════════════════════════════
WHY THIS IS NOT COSMETIC.

Before PaymentRecord, an invoice was marked paid by writing `paid_on` and a
single `payment_reference` onto the invoice itself. Those invoices are still
paid — that part is true and stays true — but they have no payment rows, so
`Invoice.amount_paid` reports zero for them and `balance` reports the full
amount as still owed.

Nothing user-facing shows that contradiction today, because the balance is only
rendered on outstanding invoices. But the ledger is now the place revenue is
summed from, and an invoice that was genuinely paid contributing zero to that
sum is money quietly missing from the books.
═══════════════════════════════════════════════════════════════════════════════

── WHY THE METHOD IS "other" ──────────────────────────────────────────────────

The old field held "an M-Pesa code, a bank reference" and did not record which.
`UQWER56RFGG` looks like an M-Pesa code, and looking like one is not knowing.
Writing a guess into a financial record so the row looks tidier is exactly the
kind of small invention that is impossible to unpick later, so these are
recorded as `other` with a note saying the method predates the field.

Anyone who knows what a given payment actually was can correct it; nobody can
correct a guess they cannot tell apart from a fact.
"""

from django.db import migrations


def create_missing_payment_rows(apps, schema_editor):
    Invoice = apps.get_model("portal", "Invoice")
    PaymentRecord = apps.get_model("portal", "PaymentRecord")

    rows = []
    for invoice in Invoice.objects.filter(status="paid"):
        if invoice.payments.exists():
            continue
        rows.append(
            PaymentRecord(
                invoice=invoice,
                method="other",
                reference=(invoice.payment_reference or "")[:64],
                amount_kes=invoice.amount_kes,
                # An invoice cannot be paid without a date, but a row written
                # before this migration existed is not something to take on
                # trust in a money table.
                paid_on=invoice.paid_on or invoice.issued_on,
                note="Recorded before payments were itemised; method not captured.",
                recorded_by=invoice.recorded_by,
            )
        )

    PaymentRecord.objects.bulk_create(rows)


def remove_them(apps, schema_editor):
    """
    Reverses only what this migration wrote, identified by its own note.

    Deleting every payment row would destroy records of money that arrived,
    which is not something a schema rollback should be able to do.
    """
    PaymentRecord = apps.get_model("portal", "PaymentRecord")
    PaymentRecord.objects.filter(
        note="Recorded before payments were itemised; method not captured."
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("portal", "0008_notification_paymentrecord_invoice_organisation_and_more"),
    ]

    operations = [
        migrations.RunPython(create_missing_payment_rows, remove_them),
    ]
