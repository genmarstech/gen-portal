"""
Signing in with Google.

═══════════════════════════════════════════════════════════════════════════════
THE THREE CLAIMS WORTH DEFENDING, AND THE TESTS THAT WOULD CATCH THEM BREAKING.

1. Google says WHO somebody is, not WHETHER they may come in. A deactivated or
   locked account must not find an open side door — is_active is how access
   ends everywhere at once, and a second sign-in path that ignored it would
   make that quietly untrue.

2. An unknown address is refused, never created. Registration attaches an
   organisation and a role; a Google login that minted accounts would route
   around both and leave orphan rows nobody asked for.

3. email_verified is the entire basis for trusting the address. Without it,
   anyone who can make a Google account asserting an address they do not own
   can sign in as that person.

The state tests matter for a fourth reason that is easy to under-rate: login
CSRF is the quiet failure. Nothing looks broken — the victim is simply working
inside the attacker's account.
═══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import base64
import json

import pytest
from django.urls import reverse
from django.utils import timezone

from accounts import google, identity
from accounts.models import EmailCode, User
from accounts.views import STATE_SESSION_KEY

pytestmark = pytest.mark.django_db

PASSWORD = "correct-horse-battery"
CLIENT_ID = "test-client-id.apps.googleusercontent.com"
CALLBACK = "https://app.genmars.co.ke/api/auth/google/callback"


@pytest.fixture(autouse=True)
def _clear_throttles():
    from django.core.cache import cache

    cache.clear()
    yield
    cache.clear()


@pytest.fixture(autouse=True)
def _configured(settings):
    settings.GOOGLE_OAUTH_CLIENT_ID = CLIENT_ID
    settings.GOOGLE_OAUTH_CLIENT_SECRET = "test-secret"
    settings.GOOGLE_OAUTH_REDIRECT_URI = CALLBACK


def _verified(email: str, **extra) -> User:
    user = User.objects.create_user(email=email, password=PASSWORD, **extra)
    identity.verify_email(user, identity.issue_code(user, EmailCode.Purpose.VERIFY).code)
    user.refresh_from_db()
    return user


def _answers(monkeypatch, *, email, email_verified=True, name="A Person"):
    """Make the token exchange return a given identity without touching Google."""

    def fake(code):
        return google.GoogleIdentity(
            email=email, email_verified=email_verified, full_name=name
        )

    monkeypatch.setattr(google, "exchange_code", fake)


def _start(client):
    """Run the opening leg so the session holds a state, and return it."""
    response = client.get(reverse("google-start"))
    assert response.status_code == 302
    return client.session[STATE_SESSION_KEY]


def _signed_in(client) -> bool:
    return "_auth_user_id" in client.session


# ── the opening leg ─────────────────────────────────────────────────────────


def test_start_sends_the_browser_to_google(client):
    response = client.get(reverse("google-start"))

    assert response.status_code == 302
    assert response["Location"].startswith(
        "https://accounts.google.com/o/oauth2/v2/auth?"
    )
    assert CLIENT_ID in response["Location"]


def test_start_puts_a_state_in_the_session(client):
    client.get(reverse("google-start"))
    assert client.session.get(STATE_SESSION_KEY)


def test_the_state_in_the_url_is_the_one_in_the_session(client):
    response = client.get(reverse("google-start"))
    from urllib.parse import parse_qs, urlparse

    sent = parse_qs(urlparse(response["Location"]).query)["state"][0]
    assert sent == client.session[STATE_SESSION_KEY]


def test_no_scope_beyond_identity_is_requested(client):
    """A scope is a thing a leaked token can do. This flow needs an address."""
    response = client.get(reverse("google-start"))
    from urllib.parse import parse_qs, urlparse

    scope = parse_qs(urlparse(response["Location"]).query)["scope"][0]
    assert set(scope.split()) == {"openid", "email", "profile"}


# ── off by default ──────────────────────────────────────────────────────────


def test_both_views_are_absent_when_unconfigured(client, settings):
    """
    Not a 500, and not a friendly error. The feature is off, and a 404 is the
    only answer that says so without inviting somebody to debug it.
    """
    settings.GOOGLE_OAUTH_CLIENT_ID = ""

    assert client.get(reverse("google-start")).status_code == 404
    assert client.get(reverse("google-callback")).status_code == 404


# ── the state, which is the login-CSRF defence ──────────────────────────────


def test_a_callback_with_no_state_in_the_session_is_refused(client, monkeypatch):
    _answers(monkeypatch, email="someone@example.com")
    _verified("someone@example.com")

    response = client.get(reverse("google-callback"), {"code": "x", "state": "made-up"})

    assert response["Location"] == "/sign-in?error=google"
    assert not _signed_in(client)


def test_a_mismatched_state_is_refused(client, monkeypatch):
    _verified("someone@example.com")
    _answers(monkeypatch, email="someone@example.com")
    _start(client)

    response = client.get(
        reverse("google-callback"), {"code": "x", "state": "not-the-one"}
    )

    assert response["Location"] == "/sign-in?error=google"
    assert not _signed_in(client)


def test_a_state_cannot_be_used_twice(client, monkeypatch):
    """
    A replayed callback is a replayed sign-in. There is no flow in which
    reusing a state is correct — a second attempt starts at /start.
    """
    _verified("someone@example.com")
    _answers(monkeypatch, email="someone@example.com")
    state = _start(client)

    first = client.get(reverse("google-callback"), {"code": "x", "state": state})
    assert first["Location"] != "/sign-in?error=google"

    client.logout()
    second = client.get(reverse("google-callback"), {"code": "x", "state": state})
    assert second["Location"] == "/sign-in?error=google"
    assert not _signed_in(client)


def test_pressing_cancel_at_google_is_not_an_error(client):
    """They are back where they started. Nothing went wrong."""
    response = client.get(reverse("google-callback"), {"error": "access_denied"})
    assert response["Location"] == "/sign-in"


def test_a_callback_with_no_code_is_refused(client):
    state = _start(client)
    response = client.get(reverse("google-callback"), {"state": state})
    assert response["Location"] == "/sign-in?error=google"


# ── who may come in ─────────────────────────────────────────────────────────


def test_a_known_verified_address_signs_in(client, monkeypatch):
    user = _verified("someone@example.com")
    _answers(monkeypatch, email="someone@example.com")
    state = _start(client)

    response = client.get(reverse("google-callback"), {"code": "x", "state": state})

    assert _signed_in(client)
    assert int(client.session["_auth_user_id"]) == user.pk
    # No organisation yet, so onboarding — the same place a password sign-in
    # would land them. Google changes who is asking, not where they belong.
    assert response["Location"] == "/onboarding"


def test_an_account_with_an_organisation_lands_on_the_dashboard(client, monkeypatch):
    """The falsifiability partner for the test above: the destination is
    computed from the account, not hardcoded to one screen."""
    user = _verified("someone@example.com")
    identity.attach_organisation(user, "A Client Company")
    _answers(monkeypatch, email="someone@example.com")
    state = _start(client)

    response = client.get(reverse("google-callback"), {"code": "x", "state": state})

    assert response["Location"] == "/dashboard"


def test_an_unknown_address_is_refused_and_creates_nothing(client, monkeypatch):
    _answers(monkeypatch, email="stranger@example.com")
    state = _start(client)

    response = client.get(reverse("google-callback"), {"code": "x", "state": state})

    assert response["Location"] == "/sign-in?error=google"
    assert not _signed_in(client)
    assert not User.objects.filter(email="stranger@example.com").exists()


def test_an_unverified_google_address_is_refused(client, monkeypatch):
    """
    The one that would be an account takeover. Without this check, making a
    Google account that asserts somebody else's address is enough to become
    them here.
    """
    _verified("someone@example.com")
    _answers(monkeypatch, email="someone@example.com", email_verified=False)
    state = _start(client)

    response = client.get(reverse("google-callback"), {"code": "x", "state": state})

    assert response["Location"] == "/sign-in?error=google"
    assert not _signed_in(client)


def test_a_deactivated_account_cannot_come_in_this_way(client, monkeypatch):
    """is_active is how access ends everywhere at once, or it is nothing."""
    user = _verified("someone@example.com")
    user.is_active = False
    user.save(update_fields=["is_active"])
    _answers(monkeypatch, email="someone@example.com")
    state = _start(client)

    response = client.get(reverse("google-callback"), {"code": "x", "state": state})

    assert response["Location"] == "/sign-in?error=google"
    assert not _signed_in(client)


def test_a_locked_account_cannot_come_in_this_way(client, monkeypatch):
    """Otherwise the lockout is a speed bump with a signposted detour."""
    user = _verified("someone@example.com")
    user.locked_until = timezone.now() + timezone.timedelta(minutes=10)
    user.save(update_fields=["locked_until"])
    _answers(monkeypatch, email="someone@example.com")
    state = _start(client)

    response = client.get(reverse("google-callback"), {"code": "x", "state": state})

    assert response["Location"] == "/sign-in?error=google"
    assert not _signed_in(client)


def test_google_cannot_be_reached_is_not_a_sign_in(client, monkeypatch):
    _verified("someone@example.com")

    def boom(code):
        raise google.GoogleError("token endpoint unreachable")

    monkeypatch.setattr(google, "exchange_code", boom)
    state = _start(client)

    response = client.get(reverse("google-callback"), {"code": "x", "state": state})

    assert response["Location"] == "/sign-in?error=google"
    assert not _signed_in(client)


# ── what a Google sign-in is allowed to change ──────────────────────────────


def test_it_verifies_an_unverified_address(client, monkeypatch):
    """Google has checked the address, so we have."""
    user = User.objects.create_user(email="new@example.com", password=PASSWORD)
    assert not user.is_email_verified

    _answers(monkeypatch, email="new@example.com")
    state = _start(client)
    client.get(reverse("google-callback"), {"code": "x", "state": state})

    user.refresh_from_db()
    assert user.is_email_verified


def test_it_does_not_restamp_an_already_verified_address(client, monkeypatch):
    """The date means when it was verified, not when somebody last signed in."""
    user = _verified("someone@example.com")
    original = user.email_verified_at

    _answers(monkeypatch, email="someone@example.com")
    state = _start(client)
    client.get(reverse("google-callback"), {"code": "x", "state": state})

    user.refresh_from_db()
    assert user.email_verified_at == original


def test_a_successful_sign_in_clears_failed_attempts(client, monkeypatch):
    user = _verified("someone@example.com")
    user.failed_sign_ins = 3
    user.save(update_fields=["failed_sign_ins"])

    _answers(monkeypatch, email="someone@example.com")
    state = _start(client)
    client.get(reverse("google-callback"), {"code": "x", "state": state})

    user.refresh_from_db()
    assert user.failed_sign_ins == 0


def test_an_unverified_account_is_sent_to_verify_not_the_dashboard(
    client, monkeypatch
):
    """
    A Google sign-in verifies the address, so the destination is the dashboard
    — but only because the verification above actually happened. This is the
    falsifiability partner for that test: if the stamping were removed, this
    would fail too rather than silently routing people to /verify forever.
    """
    User.objects.create_user(email="new@example.com", password=PASSWORD)
    _answers(monkeypatch, email="new@example.com")
    state = _start(client)

    response = client.get(reverse("google-callback"), {"code": "x", "state": state})

    assert response["Location"] in ("/dashboard", "/onboarding")


# ── reading the ID token ────────────────────────────────────────────────────


def _id_token(**claims) -> str:
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"header.{payload}.signature"


def _good_claims(**overrides):
    base = {
        "aud": CLIENT_ID,
        "iss": "https://accounts.google.com",
        "email": "someone@example.com",
        "email_verified": True,
        "name": "A Person",
    }
    base.update(overrides)
    return base


def test_a_well_formed_token_reads_back():
    got = google._claims(_id_token(**_good_claims()))
    assert got.email == "someone@example.com"
    assert got.email_verified is True
    assert got.full_name == "A Person"


def test_a_token_for_another_application_is_refused():
    """
    Defence in depth, not the main argument — the TLS channel is. But a token
    that is genuinely Google's and issued for somebody else is exactly the
    thing that check costs nothing and catches.
    """
    with pytest.raises(google.GoogleError):
        google._claims(_id_token(**_good_claims(aud="someone-elses-client-id")))


def test_a_token_from_another_issuer_is_refused():
    with pytest.raises(google.GoogleError):
        google._claims(_id_token(**_good_claims(iss="https://evil.example")))


def test_both_spellings_of_the_issuer_are_accepted():
    """Google uses both. Refusing one would break sign-in intermittently."""
    for issuer in ("https://accounts.google.com", "accounts.google.com"):
        assert google._claims(_id_token(**_good_claims(iss=issuer))).email


def test_the_string_true_counts_as_verified():
    got = google._claims(_id_token(**_good_claims(email_verified="true")))
    assert got.email_verified is True


def test_anything_else_does_not_count_as_verified():
    """The negative control for the line above — it must not accept any truthy."""
    for value in (False, "false", None, "", 1, "yes"):
        got = google._claims(_id_token(**_good_claims(email_verified=value)))
        assert got.email_verified is False, value


def test_an_address_is_lowercased():
    got = google._claims(_id_token(**_good_claims(email="Someone@Example.COM")))
    assert got.email == "someone@example.com"


def test_a_token_with_no_email_is_refused():
    claims = _good_claims()
    del claims["email"]
    with pytest.raises(google.GoogleError):
        google._claims(_id_token(**claims))


def test_something_that_is_not_a_jwt_is_refused():
    for rubbish in ("", "nope", "two.parts", "a.b.c.d"):
        with pytest.raises(google.GoogleError):
            google._claims(rubbish)


def test_a_payload_that_is_not_json_is_refused():
    payload = base64.urlsafe_b64encode(b"not json at all").decode().rstrip("=")
    with pytest.raises(google.GoogleError):
        google._claims(f"header.{payload}.signature")
