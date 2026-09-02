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

── BOTH PARTS ARE WRITTEN, NEITHER IS GENERATED ────────────────────────────
Every message goes out as text AND html. The text is not a stripped-down
fallback produced from the markup — it is written to be read on its own,
because that is what a plain-text client, a screen reader, a spam filter and
anyone with images off actually gets. A verification code that only exists
inside a <div> is a code some recipients cannot use.

Keeping them in step is a real cost and it is paid deliberately: a template
that renders the code and a text body that does not would be caught by the
tests in test_emails.py, which assert the code appears in both.
"""



from __future__ import annotations

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string

from .models import EmailCode


def _send(
    *,
    to: str,
    subject: str,
    text: str,
    template: str,
    context: dict,
) -> None:
    """
    One message, two parts.

    The HTML is attached as an alternative, so a client that cannot or will not
    render it falls back to `text` rather than to nothing. `subject` is passed
    into the context as well because the layout uses it for <title>, which is
    what some clients show when a message is opened in a browser window.
    """
    message = EmailMultiAlternatives(
        subject=subject,
        body=text,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[to],
    )
    message.attach_alternative(
        render_to_string(template, {"subject": subject, **context}),
        "text/html",
    )
    message.send(fail_silently=False)


def _minutes() -> int:
    return int(EmailCode.LIFETIME.total_seconds() // 60)


def send_verification_code(email: str, code: str) -> None:
    _send(
        to=email,
        subject="Your Genmars verification code",
        text=(
            f"Your code is {code}\n\n"
            f"It expires in {_minutes()} minutes and can be used once.\n\n"
            "If you did not create a Genmars account, you can ignore this "
            "message — nothing has been set up.\n\n"
            "Genmars Tech Limited\n"
            "genmars.co.ke"
        ),
        template="email/verification.html",
        context={
            "heading": "Confirm your email address",
            # The inbox preview line. Left unset, clients scrape the first
            # words of the body — which here is the code, on a lock screen.
            "preheader": "A code to confirm your email address.",
            "code": code,
            "minutes": _minutes(),
        },
    )


def send_password_reset_code(email: str, code: str) -> None:
    _send(
        to=email,
        subject="Reset your Genmars password",
        text=(
            f"Your reset code is {code}\n\n"
            f"It expires in {_minutes()} minutes and can be used once.\n\n"
            "If you did not ask to reset your password, you can ignore this "
            "message. Your password has not changed, and nobody can change it "
            "without this code.\n\n"
            "Genmars Tech Limited\n"
            "genmars.co.ke"
        ),
        template="email/password_reset.html",
        context={
            "heading": "Set a new password",
            "preheader": "A code to set a new password.",
            "code": code,
            "minutes": _minutes(),
        },
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
    _send(
        to=email,
        subject=f"{invited_by} has added you to {organisation} on Genmars",
        text=(
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
        template="email/invite.html",
        context={
            "heading": f"Access to {organisation}",
            "preheader": f"{invited_by} has given you access to the {organisation} portal.",
            "code": code,
            "minutes": _minutes(),
            "organisation": organisation,
            "invited_by": invited_by,
        },
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
    _send(
        to=email,
        subject=f"{reference} — progress note, week of {week_of}",
        text=(
            f"{title}\n"
            f"Week of {week_of}\n\n"
            f"{body}\n\n"
            f"Scope, milestones and every earlier note are at\n"
            f"https://app.genmars.co.ke/dashboard/{reference}\n\n"
            "Genmars Tech Limited\n"
            "genmars.co.ke"
        ),
        template="email/progress_note.html",
        context={
            "heading": title,
            # The first line of the note, so the inbox shows what happened
            # rather than repeating the subject.
            "preheader": body.strip().splitlines()[0][:120] if body.strip() else "",
            "reference": reference,
            "week_of": week_of,
            "body": body,
        },
    )


def send_staff_invite(email: str, code: str, role: str, invited_by: str) -> None:
    """
    Somebody has been added to Genmars itself.

    Says what the account can see, because it is a lot: this one reads every
    client's scope, prices and progress. Somebody accepting it should know that
    before they choose a password, not discover it afterwards.
    """
    _send(
        to=email,
        subject=f"{invited_by} has added you to Genmars Operations",
        text=(
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
        template="email/staff_invite.html",
        context={
            "heading": "Your Genmars Operations account",
            "preheader": f"{invited_by} has set up an account for you, as {role}.",
            "code": code,
            "minutes": _minutes(),
            "role": role,
            "invited_by": invited_by,
        },
    )


def send_support_raised(email: str, reference: str, subject: str, organisation: str, body: str) -> None:
    """
    Tell us a client has asked for something.

    Carries the question itself, not a link to it. An alert that says "you have
    a ticket" makes somebody sign in to find out whether it can wait — which is
    the decision the alert was supposed to help with.
    """
    _send(
        to=email,
        subject=f"{reference} — {organisation}: {subject}",
        text=(
            f"{organisation} has asked for help.\n\n"
            f"{subject}\n\n"
            f"{body}\n\n"
            f"Reply in operations: https://ops.genmars.co.ke/support\n\n"
            "Genmars Tech Limited\n"
            "genmars.co.ke"
        ),
        template="email/support_raised.html",
        context={
            "heading": subject,
            "preheader": f"{organisation}: {subject}",
            "reference": reference,
            "organisation": organisation,
            "body": body,
        },
    )


def send_support_reply(email: str, reference: str, subject: str, body: str) -> None:
    """
    Tell a client we have replied.

    The reply is in the email, for the same reason the progress note is: making
    somebody authenticate to read three sentences is friction we added, not a
    service we provided.

    ── NO RESPONSE-TIME PROMISE, EVER ──────────────────────────────────────────
    Charter 03 §IV. Not "we will follow up shortly", not "expect an update
    soon". This says what was said and stops.
    """
    _send(
        to=email,
        subject=f"Re: {subject} ({reference})",
        text=(
            f"{body}\n\n"
            f"The whole conversation is at\n"
            f"https://app.genmars.co.ke/support\n\n"
            "Genmars Tech Limited\n"
            "genmars.co.ke"
        ),
        template="email/support_reply.html",
        context={
            "heading": subject,
            "preheader": body.strip().splitlines()[0][:120] if body.strip() else "",
            "reference": reference,
            "body": body,
        },
    )


def send_data_export_notice(email: str, who: str, organisation: str, when: str) -> None:
    """
    Somebody exported everything we hold about them.

    ── WHY THIS IS RECORDED AT ALL ─────────────────────────────────────────────

    Charter 05 §VIII gives every client their data back on demand, and the
    portal does it without a human involved — which is right, and also means
    the only trace would be a log line nobody reads.

    "Who asked for their data, and when" is the first question in any
    data-protection conversation, so it goes to the address the privacy policy
    names. It is a notice, not an approval step: nothing here can or should
    stop the export.

    The export ITSELF is not attached. It is the client's personal data, and
    mailing a copy to ourselves every time somebody exercises a right would be
    a new copy of their data created by the act of respecting their privacy.
    """
    _send(
        to=email,
        subject=f"Data export — {organisation}",
        text=(
            f"{who} exported their data from the client portal.\n\n"
            f"Organisation: {organisation}\n"
            f"When: {when}\n\n"
            "This is a record, not a request. The export has already happened — "
            "Charter 05 §VIII, and nothing should stand between a client and "
            "their own data.\n\n"
            "The export is not attached. It is their personal data, and copying "
            "it to ourselves every time somebody exercises a right would create "
            "a new copy by the act of respecting their privacy.\n\n"
            "Genmars Tech Limited\n"
            "genmars.co.ke"
        ),
        template="email/data_export.html",
        context={
            "heading": "A client exported their data",
            "preheader": f"{organisation} — {when}",
            "who": who,
            "organisation": organisation,
            "when": when,
        },
    )

