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



def send_order_opened(
    *,
    email: str,
    reference: str,
    title: str,
    scope: str,
    exclusions: str,
    target_date: str,
    contact: str,
) -> None:
    """
    We have written down what a client asked for. Charter 05 §I.

    ══════════════════════════════════════════════════════════════════════════
    THIS EMAIL DOES NOT SAY WORK HAS STARTED, BECAUSE IT HAS NOT.

    Charter 02 §I puts a signed statement of work before delivery. This is sent
    when an order is opened in SCOPING — often straight after a phone call —
    and the tempting sentence, "we've started on your booking system", would be
    the company committing itself by notification instead of by contract.

    What it says instead is what is true: here is what we understood, please
    tell us if it is wrong.
    ══════════════════════════════════════════════════════════════════════════

    ── THE SCOPE AND THE EXCLUSIONS ARE IN THE MESSAGE ─────────────────────────

    Not behind a link, for the reason send_progress_note gives — but here it
    matters more. The whole value of writing scope down before work is that the
    client gets to disagree while disagreeing is cheap, and a client who has to
    remember a password first is a client who reads it in three weeks.

    ── AND THE EXCLUSIONS ARE NOT OMITTED WHEN EMPTY ───────────────────────────

    A blank exclusions field says "we have not yet said what is out of scope",
    which is honest and worth the client seeing. Quietly dropping the heading
    would let an unstated boundary look like a settled one.
    """
    limits = exclusions.strip() or (
        "We have not yet written down what is outside this. If there is "
        "something you are assuming is included, now is the moment to say so."
    )

    _send(
        to=email,
        subject=f"{reference} — {title}",
        text=(
            f"{title}\n"
            f"{reference}\n\n"
            "This is what we understood you asked for. Nothing has started "
            "yet — please read it and tell us if any of it is wrong.\n\n"
            f"WHAT IT COVERS\n{scope}\n\n"
            f"WHAT IT DOES NOT COVER\n{limits}\n\n"
            + (f"TARGET DATE\n{target_date}\n\n" if target_date else "")
            + f"Your contact at Genmars is {contact}.\n\n"
            f"https://app.genmars.co.ke/dashboard/{reference}\n\n"
            "Genmars Tech Limited\n"
            "genmars.co.ke"
        ),
        template="email/order_opened.html",
        context={
            "heading": title,
            "preheader": "What we understood you asked for — please check it.",
            "reference": reference,
            "scope": scope,
            "limits": limits,
            "target_date": target_date,
            "contact": contact,
        },
    )


def send_offer(
    *,
    email: str,
    reference: str,
    title: str,
    amount_kes: str,
    list_price_kes: str,
    expires_on: str,
    proposal: dict,
    payment_terms: str,
    next_step: str,
) -> None:
    """
    A quote or proposal, put in front of a client.

    ══════════════════════════════════════════════════════════════════════════
    THIS IS THE HALF THAT WAS MISSING, AND IT IS THE HALF THAT MATTERS.

    Sending an offer used to write a notification into the portal and stop. So
    a price sat behind a login, waiting for a client to happen to sign in — and
    from our side that is indistinguishable from having quoted somebody who
    went quiet.

    Worse, the person we talk to is usually not the person who signs off. A
    quote lives or dies on being forwardable, and one that exists only inside a
    portal that one named individual can reach is a quote the decision-maker
    never sees.
    ══════════════════════════════════════════════════════════════════════════

    ── THE WHOLE THING IS IN THE MESSAGE ───────────────────────────────────────

    Not a link to it. Same rule as send_progress_note, and the same reason:
    "you have a quote, sign in to read it" is a notification about a
    notification. The portal link is there for accepting, which is the one
    thing that genuinely needs an authenticated click.

    ── AND IT DOES NOT PRESSURE ANYBODY ────────────────────────────────────────

    The expiry is stated once, as a fact, because an open-ended price is one we
    are still bound by in a year after our costs have moved. It is not repeated,
    not counted down, and not framed as an opportunity closing — Charter 04 §III
    is specific over impressive, and a deadline used as leverage is neither.
    """
    lines = [title, reference, ""]

    headings = [
        ("context", "WHAT WE UNDERSTOOD"),
        ("approach", "HOW WE WOULD DO IT"),
        ("inclusions", "WHAT THE PRICE COVERS"),
        ("exclusions", "WHAT IT DOES NOT COVER"),
        ("timeline", "HOW LONG"),
    ]
    for key, heading in headings:
        value = (proposal.get(key) or "").strip()
        if value:
            lines += [heading, value, ""]

    lines += [f"PRICE\nKES {amount_kes}"]
    if list_price_kes:
        # What we discounted FROM. A price without its reference point is a
        # number they cannot judge, and hiding it would make the discount a
        # sales tactic rather than a fact.
        lines[-1] += f"  (list price KES {list_price_kes})"
    lines += ["", f"This price is valid until {expires_on}.", ""]

    if payment_terms.strip():
        lines += ["PAYMENT", payment_terms.strip(), ""]
    if next_step.strip():
        lines += ["IF YOU WANT TO GO AHEAD", next_step.strip(), ""]

    lines += [
        "You can read it, print it and accept it here:",
        "https://app.genmars.co.ke/offers",
        "",
        "Genmars Tech Limited",
        "genmars.co.ke",
    ]

    _send(
        to=email,
        subject=f"{title} — quote from Genmars ({reference})",
        text="\n".join(lines),
        template="email/offer.html",
        context={
            "heading": title,
            "preheader": f"KES {amount_kes}, valid until {expires_on}.",
            "reference": reference,
            "amount_kes": amount_kes,
            "list_price_kes": list_price_kes,
            "expires_on": expires_on,
            "proposal": proposal,
            "payment_terms": payment_terms.strip(),
            "next_step": next_step.strip(),
        },
    )
