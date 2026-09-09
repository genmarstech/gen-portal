"""
Signing in to a sibling application with a Genmars account.

The things that must hold, and what each one costs if it does not:

  a registered redirect address is matched WHOLE      an attacker registers
                                                      nothing and receives codes
                                                      at a lookalike host
  a code is spendable once                            a code in a log or a
                                                      referrer is a login
  a code is bound to the address it was issued for    a code lifted from one
                                                      app is spendable at another
  no account, no entry                                the whole point of one
                                                      company accounts system
  every redemption failure looks the same             a caller learns which
                                                      half of the credential
                                                      pair to keep guessing
"""

from __future__ import annotations

from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone

from accounts import identity
from accounts.models import EmailCode, Membership, Organisation, User
from portal.models import SignOnApp, SignOnGrant, System, validate_live_https_url

pytestmark = pytest.mark.django_db

PASSWORD = "correct-horse-battery"
CALLBACK = "https://business-os.genmars.co.ke/auth/callback"


@pytest.fixture(autouse=True)
def _clear_throttles():
    from django.core.cache import cache

    cache.clear()
    yield
    cache.clear()


def _verified(email: str, **extra) -> User:
    user = User.objects.create_user(email=email, password=PASSWORD, **extra)
    identity.verify_email(user, identity.issue_code(user, EmailCode.Purpose.VERIFY).code)
    user.refresh_from_db()
    return user


@pytest.fixture
def owner() -> User:
    return _verified("founder@genmars.co.ke", is_staff=True, staff_role="founder")


@pytest.fixture
def staff() -> User:
    return _verified("delivery@genmars.co.ke", is_staff=True, staff_role="delivery")


@pytest.fixture
def client_user() -> User:
    return _verified("someone@example.com")


@pytest.fixture
def system(owner) -> System:
    return System.objects.create(
        name="Business OS",
        slug="business-os",
        kind=System.Kind.INTERNAL,
        criticality=System.Criticality.IMPORTANT,
        purpose="Runs the company's own operations.",
        impact_if_down="Internal work stops; no client surface is affected.",
        owner=owner,
    )


@pytest.fixture
def app(system, owner) -> SignOnApp:
    app = SignOnApp.objects.create(
        system=system,
        client_id=identity.new_client_id(),
        redirect_uris=[CALLBACK],
        audience=SignOnApp.Audience.STAFF,
        is_enabled=True,
        created_by=owner,
    )
    app.plaintext_secret = identity.issue_client_secret(app).secret
    return app


# ─────────────────────────────────────────────────────────────────────────────
# The https rule
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "bad",
    [
        "http://business-os.genmars.co.ke/cb",   # not encrypted
        "https://localhost/cb",
        "https://localhost:3000/cb",
        "https://127.0.0.1/cb",
        "https://[::1]/cb",
        "https://192.168.1.5/cb",                # an address, not a service
        "https://business-os.test/cb",           # RFC 6761 reserved
        "https://business-os.local/cb",
        "https://internal/cb",                   # no domain at all
        "https://a.com/cb?next=x",               # ambiguous once ?code= is added
        "https://a.com/cb#frag",
        "https://user:pw@a.com/cb",
        "",
        "not a url",
    ],
)
def test_a_redirect_address_must_be_a_live_https_service(bad):
    with pytest.raises(ValidationError):
        validate_live_https_url(bad)


def test_a_real_https_address_is_accepted():
    validate_live_https_url(CALLBACK)


# ─────────────────────────────────────────────────────────────────────────────
# Who gets in
# ─────────────────────────────────────────────────────────────────────────────


def test_a_client_account_cannot_reach_a_staff_only_application(app, client_user):
    assert app.admits(client_user) is False


def test_a_staff_account_reaches_a_staff_only_application(app, staff):
    assert app.admits(staff) is True


def test_widening_the_audience_lets_a_client_in(app, client_user):
    app.audience = SignOnApp.Audience.ANY
    assert app.admits(client_user) is True


def test_an_unverified_address_is_never_handed_to_a_sibling(app):
    unverified = User.objects.create_user(
        email="new@genmars.co.ke", password=PASSWORD, is_staff=True
    )
    app.audience = SignOnApp.Audience.ANY
    assert app.admits(unverified) is False


def test_a_deactivated_account_is_refused(app, staff):
    staff.is_active = False
    assert app.admits(staff) is False


# ─────────────────────────────────────────────────────────────────────────────
# Redirect matching
# ─────────────────────────────────────────────────────────────────────────────


def test_a_redirect_is_matched_whole_not_by_prefix(app):
    assert app.allows_redirect(CALLBACK) is True
    # The classic prefix-match hole: a lookalike host that starts with the
    # registered one.
    assert app.allows_redirect("https://business-os.genmars.co.ke.attacker.com/auth/callback") is False
    assert app.allows_redirect(CALLBACK + "/deeper") is False
    assert app.allows_redirect("https://elsewhere.example.org/cb") is False


def test_an_app_is_not_usable_until_it_is_configured(system, owner):
    app = SignOnApp.objects.create(
        system=system, client_id=identity.new_client_id(), created_by=owner
    )
    assert app.is_usable is False
    assert set(app.why_not_usable()) == {
        "no redirect address registered",
        "no secret issued",
        "turned off",
    }


# ─────────────────────────────────────────────────────────────────────────────
# Describing the app to the consent screen
# ─────────────────────────────────────────────────────────────────────────────


def _describe(client, app, redirect_uri=CALLBACK):
    return client.get(
        reverse("sign-on-app"),
        {"client_id": app.client_id, "redirect_uri": redirect_uri},
    )


def test_an_unknown_application_is_not_described(client, app):
    app.client_id = identity.new_client_id()  # never saved
    assert _describe(client, app).status_code == 404


def test_an_unconfigured_application_says_what_is_missing(client, app):
    app.is_enabled = False
    app.save(update_fields=["is_enabled"])
    response = _describe(client, app)
    assert response.status_code == 409
    assert "turned off" in response.json()["detail"]


def test_an_unregistered_return_address_is_refused(client, app):
    response = _describe(client, app, "https://attacker.example.org/cb")
    assert response.status_code == 400


def test_a_signed_out_visitor_is_still_told_what_is_asking(client, app):
    body = _describe(client, app).json()
    assert body["name"] == "Business OS"
    assert body["you"] == {
        "authenticated": False,
        "email": "",
        "admitted": False,
        "blocked_because": "",
    }


def test_a_client_is_told_why_a_staff_application_is_closed(client, app, client_user):
    client.force_login(client_user)
    body = _describe(client, app).json()
    assert body["you"]["admitted"] is False
    assert body["you"]["blocked_because"] == "This application is for Genmars staff only."


# ─────────────────────────────────────────────────────────────────────────────
# The handoff
# ─────────────────────────────────────────────────────────────────────────────


def _authorize(client, app, state="xyz", redirect_uri=CALLBACK):
    return client.post(
        reverse("sign-on-authorize"),
        {"client_id": app.client_id, "redirect_uri": redirect_uri, "state": state},
        content_type="application/json",
    )


def test_signing_out_means_no_code(client, app):
    assert _authorize(client, app).status_code in (401, 403)


def test_a_client_cannot_mint_a_code_for_a_staff_application(client, app, client_user):
    client.force_login(client_user)
    assert _authorize(client, app).status_code == 403
    assert SignOnGrant.objects.count() == 0


def test_a_code_is_only_ever_issued_for_a_registered_address(client, app, staff):
    client.force_login(staff)
    response = _authorize(client, app, redirect_uri="https://attacker.example.org/cb")
    assert response.status_code == 400
    assert SignOnGrant.objects.count() == 0


def test_the_handoff_returns_the_address_with_the_code_and_the_state(client, app, staff):
    client.force_login(staff)
    target = _authorize(client, app).json()["redirect_to"]

    parts = urlsplit(target)
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == CALLBACK
    query = parse_qs(parts.query)
    assert query["state"] == ["xyz"]
    assert query["code"][0].startswith(identity.GRANT_CODE_PREFIX)

    grant = SignOnGrant.objects.get()
    assert grant.user == staff
    assert grant.used_at is None
    # Stored hashed, exactly like a password.
    assert query["code"][0] not in grant.code_hashed


# ─────────────────────────────────────────────────────────────────────────────
# Redeeming
# ─────────────────────────────────────────────────────────────────────────────


def _code_for(client, app, user):
    client.force_login(user)
    target = _authorize(client, app).json()["redirect_to"]
    client.logout()
    return parse_qs(urlsplit(target).query)["code"][0]


def _redeem(client, app, code, **overrides):
    payload = {
        "client_id": app.client_id,
        "client_secret": app.plaintext_secret,
        "code": code,
        "redirect_uri": CALLBACK,
    }
    payload.update(overrides)
    return client.post(
        reverse("sign-on-token"), payload, content_type="application/json"
    )


def test_a_sibling_learns_who_the_person_is_and_nothing_lasting(client, app, staff):
    code = _code_for(client, app, staff)
    body = _redeem(client, app, code).json()

    assert body["account"]["email"] == staff.email
    assert body["account"]["is_staff"] is True
    assert body["account"]["staff_role"] == "delivery"
    assert body["organisations"] == []
    # No token, no refresh, no scope — nothing the sibling can keep and reuse.
    assert set(body) == {"account", "organisations", "issued_at"}


def test_a_client_signing_in_brings_their_organisations(client, app, client_user):
    app.audience = SignOnApp.Audience.ANY
    app.save(update_fields=["audience"])
    org = Organisation.objects.create(name="Clips Serenity Spa")
    Membership.objects.create(user=client_user, organisation=org)

    code = _code_for(client, app, client_user)
    body = _redeem(client, app, code).json()
    assert body["organisations"] == [{"id": org.pk, "name": "Clips Serenity Spa"}]


def test_a_code_is_spendable_once(client, app, staff):
    code = _code_for(client, app, staff)
    assert _redeem(client, app, code).status_code == 200
    assert _redeem(client, app, code).status_code == 400


def test_the_wrong_secret_is_refused_and_leaves_the_code_alone(client, app, staff):
    code = _code_for(client, app, staff)
    refused = _redeem(client, app, code, client_secret="gsec_wrong")
    assert refused.status_code == 400
    # Still spendable by the real application: a wrong guess from somebody else
    # must not be a way to deny the legitimate exchange.
    assert _redeem(client, app, code).status_code == 200


def test_a_code_presented_for_the_wrong_address_is_burned(client, app, staff):
    code = _code_for(client, app, staff)
    app.redirect_uris = [CALLBACK, "https://second.genmars.co.ke/cb"]
    app.save(update_fields=["redirect_uris"])

    assert _redeem(
        client, app, code, redirect_uri="https://second.genmars.co.ke/cb"
    ).status_code == 400
    # Spent, even though the presentation was wrong.
    assert SignOnGrant.objects.get().used_at is not None
    assert _redeem(client, app, code).status_code == 400


def test_an_expired_code_is_refused(client, app, staff):
    code = _code_for(client, app, staff)
    SignOnGrant.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
    assert _redeem(client, app, code).status_code == 400


def test_an_account_deactivated_in_the_meantime_is_refused(client, app, staff):
    code = _code_for(client, app, staff)
    staff.is_active = False
    staff.save(update_fields=["is_active"])
    assert _redeem(client, app, code).status_code == 400


def test_an_application_turned_off_in_the_meantime_is_refused(client, app, staff):
    code = _code_for(client, app, staff)
    app.is_enabled = False
    app.save(update_fields=["is_enabled"])
    assert _redeem(client, app, code).status_code == 400


def test_every_redemption_failure_reads_the_same(client, app, staff):
    """
    A caller must not be able to tell a stale code from a wrong secret. Knowing
    which half was right is knowing which half to keep working on.
    """
    code = _code_for(client, app, staff)
    messages = {
        _redeem(client, app, code, client_secret="gsec_wrong").json()["detail"],
        _redeem(client, app, "gsc_never-existed").json()["detail"],
        _redeem(client, app, code, client_id=identity.new_client_id()).json()["detail"],
    }
    assert messages == {identity.GRANT_REFUSED}


def test_the_secret_is_never_readable_back(app):
    """
    Rotation returns the new secret once. Nothing on the row can produce it.
    """
    first = app.plaintext_secret
    second = identity.issue_client_secret(app).secret

    assert second != first
    assert first not in app.secret_hashed
    assert second not in app.secret_hashed
    # And the old one stops working immediately — no overlap window.
    from django.contrib.auth.hashers import check_password

    assert check_password(first, app.secret_hashed) is False
    assert check_password(second, app.secret_hashed) is True
