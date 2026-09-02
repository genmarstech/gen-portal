"""
The branded emails.

Two failure modes worth guarding, and neither of them is "the HTML looks wrong":

  1. **The code reaches only one part.** Every message goes out as text and
     html. If the code is rendered into the markup but drops out of the text
     body, a recipient on a plain-text client, a screen reader, or anyone with
     HTML disabled gets an email telling them to enter a code that is not
     there. That is invisible to whoever sent it.

  2. **The HTML is silently discarded.** The Resend backend used to send only
     `text`, so an attached alternative was accepted and thrown away. Nothing
     errored; the email simply arrived unstyled forever.
"""

from __future__ import annotations

import pytest
from django.core import mail

from accounts import emails

pytestmark = pytest.mark.django_db

CODE = "472913"


def _html(message) -> str:
    html = [c for c, mimetype in message.alternatives if mimetype == "text/html"]
    assert html, "no text/html part attached"
    return html[0]


@pytest.fixture
def sent():
    """Send one of each and hand back the outbox."""
    emails.send_verification_code("client@example.com", CODE)
    emails.send_password_reset_code("client@example.com", CODE)
    emails.send_invite("client@example.com", CODE, "Kilimani Dental", "Edwin Muchemi")
    emails.send_staff_invite("new@genmars.co.ke", CODE, "Commercial", "Edwin Muchemi")
    emails.send_progress_note(
        "client@example.com", "GM-2026-0001", "Reconciliation tool",
        "1 September 2026", "Migration finished. Testing next week.",
    )
    return mail.outbox


def test_every_email_carries_both_parts(sent):
    assert len(sent) == 5
    for message in sent:
        assert message.body.strip(), f"{message.subject}: empty text part"
        html = _html(message)
        assert html.lstrip().startswith("<!DOCTYPE html>")
        assert "</html>" in html


def test_the_code_is_in_the_text_part_too(sent):
    """
    The one that would go unnoticed. A code that exists only inside a <div> is
    a code some recipients cannot use, and nothing about sending it would look
    wrong.
    """
    with_codes = [m for m in sent if CODE in _html(m)]
    assert len(with_codes) == 4, "verification, reset, invite, staff invite"

    for message in with_codes:
        assert CODE in message.body, f"{message.subject}: code missing from text"


def test_the_brand_is_present_and_the_wordmark_is_not_faked(sent):
    """
    06-brand/README.md: never bar the A. There are no webfonts in email and
    Gmail strips inline SVG, so setting GENMARS in tracked capitals would
    produce a barred A in whatever system font ran — the one thing the brand
    rules forbid outright. The mark goes in as an image instead, and the name
    beside it is ordinary sentence-case type making no claim to be the logo.
    """
    for message in sent:
        html = _html(message)
        assert "https://genmars.co.ke/apple-icon.png" in html
        assert 'alt="Genmars"' in html
        assert "genmars.co.ke" in html
        assert "GENMARS" not in html
        # Palette, not invented colours.
        assert "#2e2b34" in html and "#f4efec" in html


def test_nothing_promises_a_response_time(sent):
    """Charter 03 §IV — never a commitment that has not been tested."""
    forbidden = ["within 24 hours", "same day", "immediately", "guarantee"]
    for message in sent:
        haystack = (message.body + _html(message)).lower()
        for phrase in forbidden:
            assert phrase not in haystack, f"{message.subject}: {phrase}"


def test_the_preheader_never_leaks_the_code(sent):
    """
    The preheader is the grey line shown beside the subject in an inbox list,
    and on a phone it lands on the lock screen. Unset, clients scrape the start
    of the body — which on a code email is the code.
    """
    for message in sent:
        html = _html(message)
        head, _, _ = html.partition("<table")
        assert CODE not in head, f"{message.subject}: code in the preheader"


def test_an_invite_names_a_person_and_the_organisation_before_the_code():
    """
    It arrives unsolicited and asks the reader to set a password — the shape of
    a phishing email. A name they recognise and the organisation they work for
    are what make it credible, so they come first.
    """
    emails.send_invite("new@example.com", CODE, "Kilimani Dental", "Edwin Muchemi")
    message = mail.outbox[-1]
    html = _html(message)

    assert "Edwin Muchemi" in html and "Kilimani Dental" in html
    assert html.index("Edwin Muchemi") < html.index(CODE)
    assert "Edwin Muchemi" in message.subject


def test_a_progress_note_carries_the_note_itself_not_a_link_to_it():
    """
    Charter 05 §III promises a written update every week. Making the client
    authenticate to read three sentences is friction we added, not a service we
    provided.
    """
    emails.send_progress_note(
        "client@example.com", "GM-2026-0001", "Reconciliation tool",
        "1 September 2026", "Migration finished. Testing next week.",
    )
    message = mail.outbox[-1]

    assert "Migration finished." in message.body
    assert "Migration finished." in _html(message)


def test_a_note_with_markup_in_it_is_escaped():
    """
    Progress notes are written by staff in a textarea and rendered into HTML.
    Django autoescapes; this proves it has not been switched off with |safe by
    someone trying to make line breaks work.
    """
    emails.send_progress_note(
        "client@example.com", "GM-2026-0001", "Title",
        "1 September 2026", "<script>alert(1)</script> and 5 < 6",
    )
    html = _html(mail.outbox[-1])

    assert "<script>" not in html
    assert "&lt;script&gt;" in html
