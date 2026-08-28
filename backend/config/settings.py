"""
Django settings — Genmars client portal.

Charter 03 §IV Tier 1 is enforced here where it can be. Anything that cannot be
enforced in settings (tested backups, monitoring that reaches a human) is in
docs/PRE-LAUNCH.md and is not satisfied by this file.
"""

from pathlib import Path

import environ
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(
    DEBUG=(bool, False),
    ALLOWED_HOSTS=(list, ["localhost", "127.0.0.1"]),
    CSRF_TRUSTED_ORIGINS=(list, []),
)
environ.Env.read_env(BASE_DIR / ".env")

# Charter 03 §III: secrets never enter the repository. No default in production.
SECRET_KEY = env("DJANGO_SECRET_KEY", default="dev-only-not-for-production")
DEBUG = env("DEBUG")
ALLOWED_HOSTS = env("ALLOWED_HOSTS")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "accounts",
    "portal",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ]
        },
    }
]

# ─────────────────────────────────────────────────────────────────────────────
# Database
# ─────────────────────────────────────────────────────────────────────────────
#
# PostgreSQL in production (Charter 03 §I). SQLite is the development default so
# a fresh checkout runs without standing up a server.

DATABASES = {"default": env.db("DATABASE_URL", default="sqlite:///dev.sqlite3")}

# ─────────────────────────────────────────────────────────────────────────────
# Identity
# ─────────────────────────────────────────────────────────────────────────────

AUTH_USER_MODEL = "accounts.User"

# Argon2 first. Django ships PBKDF2 as the default; Argon2 is the stronger
# choice and is what argon2-cffi is in requirements.txt for. PBKDF2 stays in the
# list so hashes written before this change still verify and get upgraded on the
# owner's next sign-in.
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 10},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# ─────────────────────────────────────────────────────────────────────────────
# Sessions and CSRF — Charter 03 §IV Tier 1
# ─────────────────────────────────────────────────────────────────────────────
#
# Session cookies rather than JWT in localStorage. A token readable by JavaScript
# is a token any XSS can exfiltrate; an HttpOnly cookie is not.
#
# The browser only ever talks to app.genmars.co.ke: Next proxies the portal's
# /api/* calls server-side (next.config.ts `rewrites`), so page and API requests
# are same-origin. SameSite=Lax holds and no CORS is needed anywhere.
#
# api.genmars.co.ke is the SAME Django, addressable in its own right for
# products, infrastructure and integrations. The session cookie is host-only on
# app.genmars.co.ke and is never sent there — so a fault in a future public API
# endpoint cannot be driven with a portal user's session. Keep it that way: a
# browser calling api.genmars.co.ke directly would need CORS plus a deliberate
# decision about credentials, which is a bigger change than it looks.

SESSION_ENGINE = "django.contrib.sessions.backends.db"
SESSION_COOKIE_NAME = "gm_session"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_AGE = 60 * 60 * 24 * 14
SESSION_EXPIRE_AT_BROWSER_CLOSE = False

CSRF_COOKIE_NAME = "gm_csrftoken"
CSRF_COOKIE_HTTPONLY = False  # the frontend must read it to echo it back
CSRF_COOKIE_SAMESITE = "Lax"

# The origins the browser actually sends, not the address Django binds to.
#
# In production both hostnames are listed: https://app.genmars.co.ke, whose
# Origin arrives here unchanged through Next's server-side rewrite, and
# https://api.genmars.co.ke for unsafe requests made against the API hostname
# directly.
#
# ── WHY THIS IS CHECKED RATHER THAN DEFAULTED ───────────────────────────────
# Getting this wrong fails QUIETLY in the most misleading way possible: sign-in
# still works, because DRF's SessionAuthentication only enforces CSRF once a
# request is authenticated, and an anonymous POST never reaches that check. So
# the smoke test passes, someone signs in, and then every authenticated write —
# change password, and everything added later — returns 403. Better to refuse
# to boot than to ship that.
CSRF_TRUSTED_ORIGINS = env("CSRF_TRUSTED_ORIGINS")

if not DEBUG and not CSRF_TRUSTED_ORIGINS:
    raise ImproperlyConfigured(
        "CSRF_TRUSTED_ORIGINS is empty. Set it to the public origin, e.g. "
        "CSRF_TRUSTED_ORIGINS=https://app.genmars.co.ke — without it every "
        "authenticated POST is rejected while sign-in still appears to work."
    )

# TLS is terminated by the host Caddy, which sets X-Forwarded-Proto.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SECURE_SSL_REDIRECT = False  # Caddy already does this; doing it twice loops

SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
X_FRAME_OPTIONS = "DENY"

# `manage.py check --deploy` raises two warnings that are correct to silence
# here, because both concerns are handled one layer up in Caddy — and doing them
# in BOTH places is worse than doing them in one.
#
#   W004  SECURE_HSTS_SECONDS unset. Caddy sends Strict-Transport-Security at
#         the TLS edge (deploy/genmars-portal.caddy). Django emitting it too
#         would duplicate the header on proxied responses, and the value would
#         then live in two files that can silently disagree.
#
#   W008  SECURE_SSL_REDIRECT False. Caddy already redirects HTTP to HTTPS.
#         Django redirecting as well produces a second hop, and behind a proxy
#         that terminates TLS it is the classic way to build a redirect loop.
#
# Silenced deliberately so that `check --deploy` stays USEFUL: an audit that
# always prints the same two warnings is an audit people stop reading, and a
# genuine third warning would then go unnoticed.
SILENCED_SYSTEM_CHECKS = ["security.W004", "security.W008"]

# Behind Caddy, every request arrives from 127.0.0.1. DRF needs to know how many
# proxies sit in front so it reads the real client address from X-Forwarded-For
# rather than rate-limiting the proxy — which would turn every per-IP limit into
# a single global one shared by every client.
#
# This number must match deploy/genmars-portal.caddy, which is why both app and
# api records are DNS-only (grey cloud) in Cloudflare. Turning the orange cloud
# on would make this 2 — and until it was changed, DRF would read Cloudflare's
# address as the client and collapse every per-IP limit into one global limit
# shared by all clients.
NUM_PROXIES = 1

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    # Deny by default. A view that forgets to declare permissions is closed, not
    # open — the failure mode of the opposite default is a silent data leak.
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    # JSON only. The browsable API renders a form that can POST to any endpoint
    # and is not something to expose on a production origin holding client data.
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "anon": "60/min",
        "user": "240/min",
        # Auth endpoints are throttled far harder than ordinary reads: these are
        # the ones where an attacker gets value from volume.
        "auth_sign_in": "10/min",
        "auth_sign_up": "5/hour",
        # Every code request sends an email. An unthrottled endpoint here is a
        # free way to use our domain to spam someone else's inbox.
        "auth_code": "6/hour",
        # Per-EMAIL, so rotating IPs does not buy an attacker more attempts at
        # one account, and nobody can be mail-bombed via the forgot form.
        "auth_email": "8/hour",
    },
}

# Redis in production. Throttle counters live here, so a cache that is not
# shared across workers weakens the limits — locmem is a development fallback
# only, used when no REDIS_URL is configured.
CACHES = {
    "default": (
        {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": env("REDIS_URL"),
        }
        if env("REDIS_URL", default="")
        else {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}
    )
}

# ─────────────────────────────────────────────────────────────────────────────
# Locale
# ─────────────────────────────────────────────────────────────────────────────

LANGUAGE_CODE = "en-gb"
TIME_ZONE = "Africa/Nairobi"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Real SMTP in production — Zoho, per 09-communication/README.md.
#
# In development the FILE backend beats the console one: server stdout is
# buffered on Windows, so console output is unreadable exactly when you need to
# read a verification code. Files are also easier to grep.
EMAIL_BACKEND = env(
    "EMAIL_BACKEND", default="django.core.mail.backends.filebased.EmailBackend"
)
EMAIL_FILE_PATH = env("EMAIL_FILE_PATH", default=str(BASE_DIR / "sent-emails"))
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="info@genmars.co.ke")

# SMTP, used when EMAIL_BACKEND is switched to the smtp backend in production.
# Zoho on 587 with STARTTLS — see 09-communication/README.md. Port 465 would
# need EMAIL_USE_SSL instead; setting both is a configuration error Django will
# not warn about, it will simply fail to connect.
EMAIL_HOST = env("EMAIL_HOST", default="smtp.zoho.com")
EMAIL_PORT = env.int("EMAIL_PORT", default=587)
EMAIL_USE_TLS = env.bool("EMAIL_USE_TLS", default=True)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", default="")
EMAIL_TIMEOUT = env.int("EMAIL_TIMEOUT", default=10)

# ─────────────────────────────────────────────────────────────────────────────
# Error monitoring — Charter 03 §IV Tier 1: "errors reach a human"
# ─────────────────────────────────────────────────────────────────────────────
#
# ── WHY EMAIL AND NOT SENTRY ────────────────────────────────────────────────
# Sentry is the obvious answer and is probably where this ends up. It is not
# here yet because Charter 03 §I says a new tool enters the stack only when
# something already in it genuinely cannot do the job — and for one application
# with a handful of users, SMTP we already run can do this job. Adding a hosted
# service also means routing exception payloads, which can contain client data,
# to a third-party processor while the controller/processor position with the
# ODPC is still open (Charter 03 §V).
#
# Revisit at the point this becomes noisy enough to ignore, because an alert
# channel people ignore is worse than none: it converts a real signal into
# background noise and removes the pressure to fix anything.
#
# ── WHAT ACTUALLY REACHES A HUMAN ───────────────────────────────────────────
# Django's AdminEmailHandler mails ADMINS on any un-caught 500. That is the
# floor Tier 1 asks for, and it is genuinely a human: info@genmars.co.ke is a
# monitored mailbox, not an alias into a void.
#
# It is rate-limited by Django itself only per-process, so a crash loop can
# still send a burst. Accepted for now — the alternative is a queue we would
# also have to monitor.

ADMINS = [("Genmars Tech", env("ERROR_EMAIL", default="info@genmars.co.ke"))]
SERVER_EMAIL = env("SERVER_EMAIL", default=DEFAULT_FROM_EMAIL)

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "plain": {"format": "{levelname} {name} {message}", "style": "{"},
    },
    "filters": {
        # Without this, DEBUG=True would email every exception to a human while
        # someone is working locally.
        "require_debug_false": {"()": "django.utils.log.RequireDebugFalse"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "plain"},
        "mail_admins": {
            "class": "django.utils.log.AdminEmailHandler",
            "level": "ERROR",
            "filters": ["require_debug_false"],
            # include_html=False on purpose. The HTML traceback embeds local
            # variables, which for this application means session keys, email
            # addresses and submitted form values — client personal data, sent
            # in plain text through a mail relay. The plain traceback is enough
            # to find the fault.
            "include_html": False,
        },
    },
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {
        # Auth outcomes are logged by reason, never with a password or a code.
        "accounts.identity": {"level": "INFO", "propagate": True},
        # Un-caught exceptions in a view land here. Both handlers: the console
        # for the container log, the mail for the human.
        "django.request": {
            "handlers": ["console", "mail_admins"],
            "level": "ERROR",
            "propagate": False,
        },
    },
}

if not DEBUG and not EMAIL_HOST_PASSWORD:
    # Not fatal — the app can serve requests without outbound mail. But
    # "monitoring that reaches a human" is a Tier 1 item, and silently having no
    # channel is exactly the state that looks fine until the day it matters.
    import warnings

    warnings.warn(
        "EMAIL_HOST_PASSWORD is unset: error reports cannot be delivered and "
        "verification codes will not send. Tier 1 monitoring is NOT satisfied.",
        RuntimeWarning,
    )
