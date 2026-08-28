"""
Outbound mail for the auth flows.

Two things this must never do:

  1. **Log the code.** It is a credential for its fifteen-minute life. It goes
     to the recipient and nowhere else — not into a log line, not into an error
     report, not into a Sentry breadcrumb.
  2. **Reveal whether an address is registered.** These are only ever called
     when an account exists; the caller decides that, and responds identically
     when it does not.

Voice follows Charter 04 §III — plain, specific, no inflation. These are the
first emails many clients will get from us, and "Your verification code" beats
anything with an exclamation mark in it.
"""

from __future__ import annotations

from django.conf import settings
from django.core.mail import send_mail

from .models import EmailCode


def _minutes() -> int:
    return int(EmailCode.LIFETIME.total_seconds() // 60)


def send_verification_code(email: str, code: str) -> None:
    send_mail(
        subject="Your Genmars verification code",
        message=(
            f"Your code is {code}\n\n"
            f"It expires in {_minutes()} minutes and can be used once.\n\n"
            "If you did not create a Genmars account, you can ignore this "
            "message — nothing has been set up.\n\n"
            "Genmars Tech Limited\n"
            "genmars.co.ke"
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[email],
        fail_silently=False,
    )


def send_password_reset_code(email: str, code: str) -> None:
    send_mail(
        subject="Reset your Genmars password",
        message=(
            f"Your reset code is {code}\n\n"
            f"It expires in {_minutes()} minutes and can be used once.\n\n"
            "If you did not ask to reset your password, you can ignore this "
            "message. Your password has not changed, and nobody can change it "
            "without this code.\n\n"
            "Genmars Tech Limited\n"
            "genmars.co.ke"
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[email],
        fail_silently=False,
    )
