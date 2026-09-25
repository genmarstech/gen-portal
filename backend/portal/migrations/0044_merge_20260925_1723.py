"""
Two branches, each numbering its migration 0043.

WHY THIS EXISTS AND WHY IT IS EMPTY
───────────────────────────────────
#16 added notification kinds for the statement of work; #17 added the image
fields for /products. Neither touches the other's model, so there is nothing
to reconcile — the conflict is in the graph, not in the schema, and this
records that the two lines rejoin here.

HOW IT GOT PAST CI, WHICH IS THE PART WORTH REMEMBERING
───────────────────────────────────────────────────────
Both branches were green. Each carried exactly one 0043 and passed
`makemigrations --check` against its own base; the second leaf only existed
once both were on main, and CI does not test the merged result of two open
branches. `main` then went red on a push nobody had touched.

The setting that would prevent it is "require branches to be up to date
before merging", which forces the second PR to rebase and discover the
collision while it is still one person's problem. Until that is on, two
migrations open at once is a state to watch for by eye.
"""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('portal', '0043_alter_notification_kind'),
        ('portal', '0043_workitem_image_alt_workitem_image_credit_name_and_more'),
    ]

    operations = [
    ]
