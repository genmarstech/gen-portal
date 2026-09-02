"""
Give existing staff accounts a role.

WITHOUT THIS, DEPLOYING THE PREVIOUS MIGRATION LOCKS THE COMPANY OUT.

`staff_role` defaults to empty, and an empty role can read operations and
change nothing. Every staff account that existed before this — on the live
system, exactly one, the superuser — would have woken up unable to qualify an
enquiry, issue a contract, or grant anybody the role that would fix it. A
permission model whose first act is to lock out its only administrator is not
a permission model, it is an outage.

FOUNDER rather than COMMERCIAL, for the same reason: the accounts that predate
roles are the people who set the system up, and the only safe assumption when
the alternative is nobody being able to grant anything.

Superusers specifically are also founders by definition here — `createsuperuser`
is how the first account was made, and Charter 02 §I gives the founder the
authority this role carries.
"""

from django.db import migrations


def backfill(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    User.objects.filter(is_staff=True, staff_role="").update(staff_role="founder")


def unfill(apps, schema_editor):
    """
    Reverse to empty rather than to nothing.

    Rolling back is a downgrade, and leaving a role set for a column that no
    longer exists would be fine — but if the column is re-added later this
    prevents stale values from a previous life reappearing as authority.
    """
    User = apps.get_model("accounts", "User")
    User.objects.filter(staff_role="founder").update(staff_role="")


class Migration(migrations.Migration):
    dependencies = [("accounts", "0003_user_staff_role_alter_user_is_active")]
    operations = [migrations.RunPython(backfill, unfill)]
