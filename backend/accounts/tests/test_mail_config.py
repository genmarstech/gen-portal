"""
Boot guards on outbound mail.

These do not test Django's mail machinery — they test that a MISCONFIGURATION
cannot start. The distinction matters: every failure mode guarded here is
invisible at runtime. With the file backend in production, sign-up returns 200,
the code is written to a file inside the container, and the client waits for a
message that was never sent. Nothing raises, nothing logs, nothing alerts.

Guards are re-executed with importlib rather than imported, because settings
are evaluated once at import time and the checks live at module scope.
"""

from __future__ import annotations

import importlib
import os
from contextlib import contextmanager

import pytest
from django.core.exceptions import ImproperlyConfigured

PRODUCTION = {
    "DEBUG": "False",
    "DJANGO_SECRET_KEY": "test-only-not-a-real-secret",
    "ALLOWED_HOSTS": "app.genmars.co.ke,api.genmars.co.ke",
    "CSRF_TRUSTED_ORIGINS": "https://app.genmars.co.ke",
    "EMAIL_BACKEND": "django.core.mail.backends.smtp.EmailBackend",
    "EMAIL_HOST_PASSWORD": "an-app-specific-password",
}


@contextmanager
def environment(**overrides):
    """Swap the whole environment, then restore it."""
    previous = dict(os.environ)
    os.environ.update({**PRODUCTION, **{k: v for k, v in overrides.items() if v is not None}})
    for key, value in overrides.items():
        if value is None:
            os.environ.pop(key, None)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(previous)


def reload_settings():
    import config.settings

    importlib.reload(config.settings)


def test_production_rejects_the_file_backend():
    """
    The development default. Left in place it delivers nothing while reporting
    success — the most expensive kind of working.
    """
    with environment(EMAIL_BACKEND="django.core.mail.backends.filebased.EmailBackend"):
        with pytest.raises(ImproperlyConfigured, match="does not send mail"):
            reload_settings()


@pytest.mark.parametrize(
    "backend",
    [
        "django.core.mail.backends.console.EmailBackend",
        "django.core.mail.backends.dummy.EmailBackend",
        "django.core.mail.backends.locmem.EmailBackend",
    ],
)
def test_production_rejects_every_non_sending_backend(backend):
    with environment(EMAIL_BACKEND=backend):
        with pytest.raises(ImproperlyConfigured):
            reload_settings()


def test_production_requires_a_password():
    """The SMTP fallback path. Zoho is still what the human mailbox uses."""
    with environment(EMAIL_HOST_PASSWORD=None):
        with pytest.raises(ImproperlyConfigured, match="EMAIL_HOST_PASSWORD"):
            reload_settings()


# ── Resend ──────────────────────────────────────────────────────────────────
# The intended production configuration. Transactional mail — verification
# codes, password resets, error alerts — goes through Resend's HTTP API.

RESEND = "accounts.mail_backends.ResendBackend"


def test_production_requires_an_api_key_when_using_resend():
    """
    Same failure as an empty Zoho password, different credential. Booting
    without it means every sign-up returns 200 and no code is ever sent.

    Set to "" rather than unset, for the same reason as the trusted-origins
    test below: settings read backend/.env, so on any machine with a real key
    in that file, unsetting the environment variable lets django-environ supply
    it from the file and this test passes without exercising the guard at all.
    """
    with environment(EMAIL_BACKEND=RESEND, RESEND_API_KEY="", EMAIL_HOST_PASSWORD=None):
        with pytest.raises(ImproperlyConfigured, match="RESEND_API_KEY"):
            reload_settings()


def test_resend_boots_without_a_zoho_password():
    """
    The guard must check the credential the CONFIGURED backend needs, not the
    one the previous backend needed.

    This is the regression that matters. When the SMTP check ran
    unconditionally, moving to Resend meant either keeping a Zoho password in
    the environment purely to satisfy a check that no longer applied — a secret
    kept alive for no reason — or the app refusing to boot on a correct
    configuration.
    """
    with environment(
        EMAIL_BACKEND=RESEND,
        RESEND_API_KEY="re_a_real_looking_key",
        EMAIL_HOST_PASSWORD=None,
    ):
        reload_settings()


def test_resend_does_not_excuse_a_non_sending_backend():
    """A key present but the file backend selected is still a silent outage."""
    with environment(
        EMAIL_BACKEND="django.core.mail.backends.filebased.EmailBackend",
        RESEND_API_KEY="re_a_real_looking_key",
    ):
        with pytest.raises(ImproperlyConfigured, match="does not send mail"):
            reload_settings()


@pytest.mark.parametrize("value", ["", " ", " , "])
def test_production_requires_real_trusted_origins(value):
    """
    Empty is obvious. The whitespace cases are the ones that used to slip
    through: django-environ splits on commas and keeps what it finds, so
    "CSRF_TRUSTED_ORIGINS= " parsed to [" "] — truthy, past the check, and
    worth nothing.

    Set explicitly rather than unset: settings read backend/.env, so a developer
    with a local .env would otherwise supply the value and the test would prove
    nothing.
    """
    with environment(CSRF_TRUSTED_ORIGINS=value):
        with pytest.raises(ImproperlyConfigured, match="CSRF_TRUSTED_ORIGINS"):
            reload_settings()


def test_a_correct_production_configuration_boots():
    """The guards must not be so strict that a valid deployment cannot start."""
    with environment():
        reload_settings()


def test_development_is_left_alone():
    """
    None of these guards may fire with DEBUG=True. A developer running locally
    has neither a Zoho password nor a Resend key, and should need neither —
    the file backend writes codes to backend/sent-emails/ where they can be
    read.
    """
    with environment(
        DEBUG="True",
        EMAIL_BACKEND=None,
        EMAIL_HOST_PASSWORD=None,
        RESEND_API_KEY=None,
    ):
        reload_settings()


def teardown_module():
    """Restore the real settings for every test that runs after this module."""
    reload_settings()
