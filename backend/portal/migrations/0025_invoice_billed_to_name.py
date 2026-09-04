"""
Snapshot who each invoice was billed to.

The column, and a backfill of every invoice that already exists. The backfill
is the half that matters: without it the field would exist and be empty, the
serializer would fall back to the live organisation name, and the first rename
after this deploy would still rewrite the "To:" line on every invoice already
issued, sent and paid.
"""

from django.db import migrations, models


def snapshot_existing(apps, schema_editor):
    """
    Freeze the name every already-issued invoice is currently showing.

    What this writes is exactly what those documents display TODAY, so nothing
    a client has seen changes. It only stops them changing from here on.
    """
    Invoice = apps.get_model("portal", "Invoice")
    for invoice in Invoice.objects.select_related("organisation").iterator():
        if not invoice.billed_to_name:
            invoice.billed_to_name = invoice.organisation.name
            invoice.save(update_fields=["billed_to_name"])


def unsnapshot(apps, schema_editor):
    """Reversing this drops the column, so there is nothing to undo."""


class Migration(migrations.Migration):

    dependencies = [
        ("portal", "0024_contactattachment"),
    ]

    operations = [
        migrations.AddField(
            model_name="invoice",
            name="billed_to_name",
            field=models.CharField(blank=True, max_length=200),
        ),
        migrations.RunPython(snapshot_existing, unsnapshot),
    ]
