"""
Test settings.

SQLite so the suite runs anywhere without a Postgres service. The production
database is Postgres (Charter 03 §I); nothing in these tests depends on
engine-specific behaviour, and the isolation tests exercise the ORM's filtering,
which is identical across backends.
"""
from .settings import *  # noqa: F401,F403

DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

# Argon2 is slow by design. Tests do not need that cost, and using the fast
# hasher here keeps the suite quick without weakening anything in production.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
