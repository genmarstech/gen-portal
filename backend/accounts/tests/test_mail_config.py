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
    with environment(EMAIL_HOST_PASSWORD=None):
        with pytest.raises(ImproperlyConfigured, match="EMAIL_HOST_PASSWORD"):
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
    has no Zoho password and should not need one.
    """
    with environment(DEBUG="True", EMAIL_BACKEND=None, EMAIL_HOST_PASSWORD=None):
        reload_settings()


def teardown_module():
    """Restore the real settings for every test that runs after this module."""
    reload_settings()
