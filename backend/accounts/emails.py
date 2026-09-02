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


def send_invite(
    email: str, code: str, organisation: str, invited_by: str
) -> None:
    """
    Somebody at Genmars has added this person to a client organisation.

    ── WHY IT NAMES WHO INVITED THEM ───────────────────────────────────────────
    This arrives unsolicited, from a company the recipient may not have heard
    of, and asks them to set a password. That is the exact shape of a phishing
    email. The two things that make it credible are a human name they can
    recognise and the organisation they already work for — so both are in the
    first sentence, before the code.

    It also says plainly that the account already exists and cannot be signed
    into, which is true and is the reassurance that matters: nobody, including
    us, can use it until they choose a password.
    """
    send_mail(
        subject=f"{invited_by} has added you to {organisation} on Genmars",
        message=(
            f"{invited_by} at Genmars Tech has given you access to the "
            f"{organisation} client portal.\n\n"
            f"Your code is {code}\n\n"
            f"Go to https://app.genmars.co.ke/invite and enter it to choose a "
            "password. The code expires in "
            f"{_minutes()} minutes and can be used once.\n\n"
            "The account exists already but cannot be signed into by anyone — "
            "including us — until you set that password.\n\n"
            "The portal shows the scope of work we have agreed, a written "
            "progress note each week, and what has been invoiced and paid.\n\n"
            "If you were not expecting this, you can ignore it and nothing "
            "happens. The code expires on its own.\n\n"
            "Genmars Tech Limited\n"
            "genmars.co.ke"
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[email],
        fail_silently=False,
    )


def send_progress_note(
    email: str, reference: str, title: str, week_of: str, body: str
) -> None:
    """
    A weekly progress note has been published. Charter 05 §III.

    ── THE NOTE ITSELF IS IN THE EMAIL, NOT A LINK ─────────────────────────────
    A "you have an update, sign in to read it" email is a notification about a
    notification. The charter promises the client a written update every week;
    making them authenticate to read three sentences is friction we added, not
    a service we provided. The portal link is there for anything more.

    ── NO RESPONSE-TIME PROMISE, NO CALL TO ACTION ─────────────────────────────
    Charter 03 §IV standing rule: never put a commitment in front of a client
    that has not been tested under real conditions. This reports what happened
    and stops.
    """
    send_mail(
        subject=f"{reference} — progress note, week of {week_of}",
        message=(
            f"{title}\n"
            f"Week of {week_of}\n\n"
            f"{body}\n\n"
            f"Scope, milestones and every earlier note are at\n"
            f"https://app.genmars.co.ke/dashboard/{reference}\n\n"
            "Genmars Tech Limited\n"
            "genmars.co.ke"
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[email],
        fail_silently=False,
    )


def send_staff_invite(email: str, code: str, role: str, invited_by: str) -> None:
    """
    Somebody has been added to Genmars itself.

    Says what the account can see, because it is a lot: this one reads every
    client's scope, prices and progress. Somebody accepting it should know that
    before they choose a password, not discover it afterwards.
    """
    send_mail(
        subject=f"{invited_by} has added you to Genmars Operations",
        message=(
            f"{invited_by} has set up a Genmars account for you, as {role}.\n\n"
            f"Your code is {code}\n\n"
            f"Go to https://ops.genmars.co.ke and sign in, or set your password "
            "at https://app.genmars.co.ke/invite first. The code expires in "
            f"{_minutes()} minutes and can be used once.\n\n"
            "The account cannot be signed into by anyone — including whoever "
            "invited you — until you set that password.\n\n"
            "It gives you access to client work across the whole company: "
            "scope, prices, progress and contracts. Treat it accordingly.\n\n"
            "Genmars Tech Limited\n"
            "genmars.co.ke"
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[email],
        fail_silently=False,
    )
