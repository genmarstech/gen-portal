"""
Enrol an admin account's authenticator, from a shell on the server.

── WHY A COMMAND AND NOT A WEB PAGE ────────────────────────────────────────────

The enrolment secret is the whole of the second factor. A page that displays it
has put it through a browser, a TLS terminator, a proxy log and whatever else
is in the path — and it has made "can reach this page while signed in"
equivalent to "can enrol a new phone", which is exactly the escalation somebody
with a stolen session wants.

A terminal on the box is a place only somebody who already has the server has.
For a company this size that is the right trade: enrolling a person is rare,
and a web flow would weaken the control it is setting up.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from accounts import totp
from accounts.models import StaffTotp


class Command(BaseCommand):
    help = "Set up an authenticator app for a staff account."

    def add_arguments(self, parser):
        parser.add_argument("email")
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Replace an existing authenticator. The old one stops working.",
        )

    def handle(self, *args, **options):
        User = get_user_model()
        email = options["email"].strip().lower()

        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            raise CommandError(f"No account for {email}.") from None

        if not user.is_staff:
            raise CommandError(
                f"{email} is not staff, so it cannot open the admin and has "
                "nothing to protect here."
            )

        existing = StaffTotp.objects.filter(user=user).first()
        if existing and existing.is_confirmed and not options["reset"]:
            raise CommandError(
                f"{email} already has an authenticator. Pass --reset to "
                "replace it — the old one stops working immediately."
            )

        secret = totp.new_secret()
        uri = totp.provisioning_uri(secret, account=user.email, issuer="Genmars")

        self.stdout.write("")
        self.stdout.write("Add this to your authenticator app:")
        self.stdout.write("")
        self.stdout.write(f"  Secret: {secret}")
        self.stdout.write(f"  URI:    {uri}")
        self.stdout.write("")
        self.stdout.write(
            "Most apps take the URI as a QR code. Paste it into a generator "
            "you trust, or type the secret in by hand."
        )
        self.stdout.write("")

        # ── CONFIRMED BEFORE IT COUNTS ──────────────────────────────────────
        # Saving the row and trusting the app to have worked is how somebody
        # locks themselves out with a mistyped secret. Nothing is written as
        # confirmed until a code the app generated comes back.
        code = input("Type the code your app shows, to confirm: ").strip()
        if totp.verify(secret, code) is None:
            raise CommandError(
                "That code did not match. Nothing has been saved and the "
                "account is unchanged. Run it again."
            )

        device = existing or StaffTotp(user=user)
        device.secret = secret
        device.last_step = totp.current_step()
        device.confirmed_at = timezone.now()
        device.save()

        self.stdout.write(
            self.style.SUCCESS(f"\nDone. {email} now needs a code to open the admin.")
        )
