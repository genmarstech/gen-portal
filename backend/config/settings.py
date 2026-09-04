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
    # Staff-facing operations API. Same accounts, same database, same rows the
    # client portal reads — the write side of them. See operations/permissions.py
    # for why it does not reuse portal/selectors.py.
    "operations",
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
# Stripped and emptied-out, because django-environ splits on commas and keeps
# whatever it finds: a stray "CSRF_TRUSTED_ORIGINS= " parses to [" "], which is
# truthy and would sail past the check below while being worth nothing. A
# trailing comma does the same thing.
CSRF_TRUSTED_ORIGINS = [o.strip() for o in env("CSRF_TRUSTED_ORIGINS") if o.strip()]

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
    # One error shape everywhere: {"detail": ..., "field": ...}. DRF's default
    # for a validation error puts the field name in the KEY and leaves `detail`
    # unset, which every frontend here reads as "unknown error" and replaces
    # with a generic message. See config/exceptions.py.
    "EXCEPTION_HANDLER": "config.exceptions.api_exception_handler",
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
        # Registered systems reporting in. Generous, because a heartbeat every
        # minute from a dozen systems is normal traffic — but bounded, because
        # this is the one endpoint reachable with a token rather than a session,
        # and a leaked key should not be able to fill the events table.
        "system": "120/min",
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

# ─────────────────────────────────────────────────────────────────────────────
# Uploaded files
# ─────────────────────────────────────────────────────────────────────────────
#
# ══════════════════════════════════════════════════════════════════════════
# NOTHING UNDER MEDIA_ROOT IS EVER SERVED BY THE WEB SERVER.
#
# There is no Caddy `file_server` for this path and there must not be one.
# Every file here arrived from outside — a client's photo of a booking sheet,
# a PDF somebody forwarded — and two things follow from that:
#
#   · It is confidential (Charter 05 §V). A public path would hand one
#     client's document to anyone who guessed the URL, and the URL is the only
#     thing standing between them.
#   · It is untrusted. A file the browser renders IN our origin — HTML, SVG —
#     is stored cross-site scripting against a signed-in operations session.
#
# Both are handled in ONE place, portal.attachments.AttachmentDownloadView,
# which checks the session and always sends Content-Disposition: attachment.
# ══════════════════════════════════════════════════════════════════════════
MEDIA_ROOT = env("MEDIA_ROOT", default=str(BASE_DIR / "media"))

# Deliberately empty. Django only uses MEDIA_URL to build public links, and
# there is no public link — a value here would be an invitation to add one.
MEDIA_URL = ""

# Anything larger is written to a temp file rather than held in memory, so a
# handful of concurrent uploads cannot exhaust the container.
FILE_UPLOAD_MAX_MEMORY_SIZE = 2 * 1024 * 1024

# The hard ceiling on a whole request body. Below this, gunicorn and Django
# would happily buffer a 500 MB "photo" before anything got the chance to
# refuse it. The per-file rule that users actually meet lives in
# portal/attachments.py, which can explain itself; this is the backstop.
DATA_UPLOAD_MAX_MEMORY_SIZE = 12 * 1024 * 1024

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ── OUTBOUND MAIL ───────────────────────────────────────────────────────────
#
# Resend in production, over its HTTP API — accounts/mail_backends.py explains
# why the API and not SMTP, and why it added no dependency to do it.
#
# Two systems send as @genmars.co.ke and they do different jobs. Do not
# consolidate them without reading 09-communication/README.md first:
#
#   Zoho    the human mailbox. info@genmars.co.ke, what a person reads and
#           replies from. Unchanged.
#   Resend  transactional only. Verification codes, password resets, error
#           alerts. Nobody reads a reply to these.
#
# DNS. Verified live 2026-09-01, and the arrangement is worth understanding
# before anyone "tidies" it:
#
#     resend._domainkey.genmars.co.ke  TXT   DKIM public key
#     send.genmars.co.ke               TXT   v=spf1 include:amazonses.com ~all
#     send.genmars.co.ke               MX    feedback-smtp.eu-west-1.amazonses.com
#     genmars.co.ke                    TXT   v=spf1 include:zohomail.com ~all
#
# ⚠ DO NOT ADD Resend's include to the ROOT SPF record. It is a natural
#   instinct and it is wrong here. Resend sends with the envelope-from on
#   send.genmars.co.ke — a bounce subdomain that carries its OWN SPF record,
#   already published and verified — and SPF is evaluated against the envelope
#   domain, not the From: header. The root record exists for Zoho, which does
#   send as the root. Adding include:amazonses.com to it would authorise all of
#   Amazon SES to send as our root envelope domain in exchange for nothing.
#
#   The rule the instinct comes from is still true and still worth keeping:
#   there must be exactly ONE SPF TXT record on any given name. Two is a
#   permerror, and a permerror fails every sender on that name at once. It just
#   does not apply here, because these are two different names.
#
#   DMARC passes on both paths: DKIM signs d=genmars.co.ke (strict alignment),
#   and send.genmars.co.ke aligns with the root under relaxed alignment, which
#   is the default.
#
#     DKIM   `resend` sits alongside Zoho's `zmail`. Selectors are independent;
#            adding one does not disturb the other.
#     DMARC  p=none today, so nothing to change to start. Read the reports
#            before tightening — with two senders there is more to get wrong,
#            and p=quarantine on a misaligned sender silently bins verification
#            codes.
#
# In development the FILE backend beats the console one: server stdout is
# buffered on Windows, so console output is unreadable exactly when you need to
# read a verification code. Files are also easier to grep.
EMAIL_BACKEND = env(
    "EMAIL_BACKEND", default="django.core.mail.backends.filebased.EmailBackend"
)
EMAIL_FILE_PATH = env("EMAIL_FILE_PATH", default=str(BASE_DIR / "sent-emails"))
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="info@genmars.co.ke")

RESEND_BACKEND = "accounts.mail_backends.ResendBackend"

# The API key. Secret: it can send mail as us to anyone, so it belongs in
# backend/.env (git-ignored) and nowhere else. Rotate it in the Resend
# dashboard if it is ever printed, pasted, or committed.
RESEND_API_KEY = env("RESEND_API_KEY", default="")

# SMTP, retained as the fallback path — if Resend is ever unreachable, pointing
# EMAIL_BACKEND at Django's smtp backend with Zoho credentials restores mail
# without a code change. Zoho on 587 with STARTTLS, per
# 09-communication/README.md. Port 465 would need EMAIL_USE_SSL instead; setting
# both is a configuration error Django will not warn about, it will simply fail
# to connect.
EMAIL_HOST = env("EMAIL_HOST", default="smtp.zoho.com")
EMAIL_PORT = env.int("EMAIL_PORT", default=587)
EMAIL_USE_TLS = env.bool("EMAIL_USE_TLS", default=True)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", default="")

# Applies to both backends: Django's SMTP backend reads it, and the Resend
# backend passes it to urlopen(). A request that hangs holds a gunicorn worker,
# and enough of those is an outage.
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
# floor Tier 1 asks for.
#
# It goes to an OPERATOR mailbox, not to the address on the website. This line
# used to read info@genmars.co.ke, described in a comment as "a monitored
# mailbox, not an alias into a void" — which was untrue: no mailbox existed
# behind it, Zoho answered 550, Resend suppressed the address, and every alert
# was dropped silently for a day and a half while the API kept returning 200.
#
# The published address attracts whatever the public sends it and its
# deliverability is somebody else's to break. Alerting is a different job and
# gets a different address, so a client-facing bounce can never take internal
# monitoring down again. accounts/mail_health.py now watches for the failure
# mode either way.
#
# It is rate-limited by Django itself only per-process, so a crash loop can
# still send a burst. Accepted for now — the alternative is a queue we would
# also have to monitor.

ADMINS = [("Genmars Tech", env("ERROR_EMAIL", default="edwin@genmars.co.ke"))]

# Where a new support request is announced.
#
# ── THE ORDER THIS WAS DONE IN IS THE POINT ─────────────────────────────────
# It first defaulted to a mailbox already known to receive, NOT to support@,
# because pointing an alert at an address before its mailbox exists is exactly
# how GM-INC-2026-0001 happened: Zoho answered 550, Resend suppressed the
# address, and thirty-one hours of alerts were dropped in silence while the API
# kept returning 200.
#
# The alias now exists and was proven — a message sent to it on 2026-09-02
# delivered in six seconds, and a real ticket raised through the full code path
# after that delivered too — so this is the default. Anything pointed here in
# future gets the same treatment: prove the mailbox, then point at it.
SUPPORT_EMAIL = env("SUPPORT_EMAIL", default="support@genmars.co.ke")

# ── privacy@ and security@ ──────────────────────────────────────────────────
#
# Both aliases exist and both were proven to deliver before anything was
# pointed at them, which is the order every address here gets: prove the
# mailbox, then route to it. See GM-INC-2026-0001 for what the other order
# costs.
#
# privacy@ is where a data-subject request lands. The privacy policy names it,
# and the portal copies it on the one privacy-relevant thing the software does
# by itself — a client exporting everything we hold about them, Charter 05
# §VIII. That is a record worth having: "who asked for their data and when" is
# the first question in any data-protection conversation.
PRIVACY_EMAIL = env("PRIVACY_EMAIL", default="privacy@genmars.co.ke")

# security@ is where a vulnerability report arrives. Nothing in this codebase
# sends to it — a security mailbox exists to RECEIVE, and routing our own
# alerts there would bury a researcher's report under our own noise. What makes
# it work is /.well-known/security.txt on the website, which is how a finder
# knows where to write at all.
SECURITY_EMAIL = env("SECURITY_EMAIL", default="security@genmars.co.ke")
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

# ── OUTBOUND MAIL IS LOAD-BEARING, SO IT IS CHECKED AT BOOT ─────────────────
#
# This was a warning. It is now fatal, because the failure it describes is
# invisible from the outside and the app is not usable without mail:
#
#   · sign-up issues a code and returns 200
#   · onboarding cannot start until that code is entered
#   · password reset is the only recovery path there is
#   · un-caught 500s are supposed to reach a human
#
# With the FILE backend left in place — which is the DEVELOPMENT DEFAULT, so it
# is what you get by simply not setting EMAIL_BACKEND — every one of those
# "succeeds". Django writes the message to a file inside the container, returns
# cleanly, and the client sits waiting for a code that was never sent. Nothing
# in any log says so, and the container quietly accumulates files containing
# live verification codes.
#
# A portal nobody can sign up to is not degraded, it is broken. Refuse to boot.

_UNSENDABLE_BACKENDS = (
    "django.core.mail.backends.filebased.EmailBackend",
    "django.core.mail.backends.console.EmailBackend",
    "django.core.mail.backends.dummy.EmailBackend",
    "django.core.mail.backends.locmem.EmailBackend",
)

if not DEBUG:
    if EMAIL_BACKEND in _UNSENDABLE_BACKENDS:
        raise ImproperlyConfigured(
            f"EMAIL_BACKEND is {EMAIL_BACKEND!r}, which does not send mail. "
            f"Production needs EMAIL_BACKEND={RESEND_BACKEND} — without it "
            "sign-up, verification, password reset and error alerts all appear "
            "to succeed while delivering nothing."
        )

    # A configured backend with no credential is the same outage as no backend
    # at all, so each one is checked for the credential IT needs. Checking the
    # wrong one is worse than checking nothing: it passes, and the reassurance
    # is false.
    if EMAIL_BACKEND == RESEND_BACKEND:
        if not RESEND_API_KEY:
            raise ImproperlyConfigured(
                "RESEND_API_KEY is empty while EMAIL_BACKEND is the Resend "
                "backend. Create a key in the Resend dashboard and set it in "
                "backend/.env. Without it every verification code is dropped."
            )
    elif EMAIL_BACKEND == "django.core.mail.backends.smtp.EmailBackend":
        if not EMAIL_HOST_PASSWORD:
            raise ImproperlyConfigured(
                "EMAIL_HOST_PASSWORD is empty. Zoho requires an application-"
                "specific password (Zoho Mail > Settings > Security > App "
                "Passwords), not the account password, when two-factor is on."
            )


# ── billing identity ─────────────────────────────────────────────────────────
#
# What appears on an invoice document.
#
# EVERY ONE OF THESE DEFAULTS TO EMPTY, AND AN EMPTY FIELD IS OMITTED FROM THE
# DOCUMENT RATHER THAN RENDERED BLANK OR GUESSED. Charter 04 §IV — nothing
# untrue on a Genmars surface — and an invoice is the most consequential surface
# there is: a wrong KRA PIN or paybill on a document a client pays against is
# not a cosmetic bug.
#
# So the invoice shows what it has been told and no more. A document with no
# payment details says so plainly and points at the named contact, which is
# survivable; one carrying a plausible-looking wrong number is not.
BILLING_LEGAL_NAME = env("BILLING_LEGAL_NAME", default="Genmars Tech Limited")
BILLING_EMAIL = env("BILLING_EMAIL", default=DEFAULT_FROM_EMAIL)

# Kenya Revenue Authority PIN. Required on a tax invoice; omitted until set,
# because an invoice carrying an invented PIN is a document nobody can file.
BILLING_KRA_PIN = env("BILLING_KRA_PIN", default="")

BILLING_POSTAL_ADDRESS = env("BILLING_POSTAL_ADDRESS", default="")

# M-Pesa paybill / till, and what the client should put in the account field.
# ACCOUNT_HINT supports {number}, replaced with the invoice number — putting the
# invoice number in the account field is what makes a payment reconcilable
# without a phone call.
BILLING_MPESA_PAYBILL = env("BILLING_MPESA_PAYBILL", default="")
BILLING_MPESA_ACCOUNT_HINT = env("BILLING_MPESA_ACCOUNT_HINT", default="{number}")

# Free text — bank name, branch, account name and number, as they should be
# typed. One field rather than five because bank details vary in shape and a
# rigid schema would force a wrong one.
BILLING_BANK_DETAILS = env("BILLING_BANK_DETAILS", default="")

# Standard payment terms, shown on every invoice that does not override them.
BILLING_TERMS = env(
    "BILLING_TERMS",
    default="Payment is due on the date shown. Quote the invoice number as the account reference.",
)

# ── M-Pesa (Daraja) ──────────────────────────────────────────────────────────
#
# Key names match the .env the credentials arrived in rather than a tidier set
# of my own: one spelling of a secret is hard enough to keep straight across a
# host, a container and a CI runner.
#
# ── THIS IS A BUY GOODS (TILL) SETUP, NOT PAYBILL ───────────────────────────
#
# The distinction decides two fields and is the classic way an STK integration
# fails with a misleading error:
#
#   BusinessShortCode = MPESA_SHORT_CODE   the HEAD OFFICE / store number
#   PartyB            = MPESA_TILL_NUMBER  the till customers actually pay
#
# For CustomerPayBillOnline both are the paybill and nobody notices the
# difference. For CustomerBuyGoodsOnline they are different numbers, and
# sending the till as BusinessShortCode produces an invalid-credential error
# that reads as though the passkey is wrong.
#
# The password is base64(BusinessShortCode + Passkey + Timestamp) — the SHORT
# CODE, not the till.
MPESA_BASE_URL = env("MPESA_BASE_URL", default="https://sandbox.safaricom.co.ke")
MPESA_CONSUMER_KEY = env("MPESA_CONSUMER_KEY", default="")
MPESA_CONSUMER_SECRET = env("MPESA_CONSUMER_SECRET", default="")
MPESA_CONSUMER_PASSKEY = env("MPESA_CONSUMER_PASSKEY", default="")
MPESA_SHORT_CODE = env("MPESA_SHORT_CODE", default="")
MPESA_TILL_NUMBER = env("MPESA_TILL_NUMBER", default="")
MPESA_TRANSACTION_TYPE = env(
    "MPESA_TRANSACTION_TYPE", default="CustomerBuyGoodsOnline"
)

# Where Safaricom POSTs the result. Must be public HTTPS — they will not call a
# private address, and a wrong one fails silently: the customer pays, and the
# invoice sits unpaid because nothing ever told us.
MPESA_CALLBACK_URL = env(
    "MPESA_CALLBACK_URL", default="https://api.genmars.co.ke/api/mpesa/callback"
)

# A shared secret in the callback path, so the endpoint is not simply open to
# anyone who guesses the URL. Daraja sends no signature and no auth header, so
# this plus the CheckoutRequestID lookup is what stands between the callback
# and a forged "this invoice is paid".
MPESA_CALLBACK_TOKEN = env("MPESA_CALLBACK_TOKEN", default="")

# DERIVED, never a flag somebody can set by hand. Every code path checks this,
# and it cannot be true without real credentials — so "M-Pesa is on" and "we
# can actually take a payment" are the same statement.
MPESA_ENABLED = all(
    [
        MPESA_BASE_URL,
        MPESA_CONSUMER_KEY,
        MPESA_CONSUMER_SECRET,
        MPESA_CONSUMER_PASSKEY,
        MPESA_SHORT_CODE,
        MPESA_TILL_NUMBER,
        MPESA_CALLBACK_URL,
    ]
)

# Live money. api.safaricom.co.ke prompts a real phone for real shillings;
# sandbox does not. Surfaced so logs and the ops UI can say which one is in
# play — a test against production that nobody realised was production is an
# expensive way to find out.
MPESA_IS_LIVE = "sandbox" not in MPESA_BASE_URL
