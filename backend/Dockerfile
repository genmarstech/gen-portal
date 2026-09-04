# syntax=docker/dockerfile:1.7

# ─────────────────────────────────────────────────────────────────────────────
# Genmars client portal — Django API
#
# ── HOW THIS DIFFERS FROM gen-website ───────────────────────────────────────
# The marketing site's image contains no application code and no runtime: it is
# a web server and a folder of files, so it cannot have a dependency CVE at
# runtime because it has no dependencies at runtime.
#
# That argument does NOT hold here and must not be copied over. This image runs
# Python, Django, DRF, psycopg and Celery against a database holding client
# personal data. It has a real dependency surface and a real blast radius. What
# replaces the old argument:
#
#   · a pinned interpreter (see below — 3.13, not 3.14, not "3")
#   · no compiler or build headers in the final stage
#   · a non-root user that owns nothing it does not need
#   · no shell history, no pip cache, no .git
#
# ── WHY 3.13 IS PINNED ──────────────────────────────────────────────────────
# Django 5.1 supports Python 3.10–3.13. On 3.14 the admin raises
# `AttributeError: 'super' object has no attribute 'dicts'` on any changelist
# render, which removes the only way staff create orders in v1. A floating tag
# (`python:3-slim`) would introduce that silently on some future rebuild, and it
# would pass every API smoke test, because nothing outside the admin templates
# touches that code path. Revisit when Django supports 3.14 (6.0 does).
# ─────────────────────────────────────────────────────────────────────────────


# ---- build ------------------------------------------------------------------

FROM python:3.13-slim AS build

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# argon2-cffi and psycopg build C extensions. The toolchain to do that lives
# HERE and never reaches the runtime stage — a compiler in production is a
# convenience for an attacker and for nobody else.
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends build-essential libpq-dev; \
    rm -rf /var/lib/apt/lists/*

# A virtualenv rather than the system site-packages, so the runtime stage can
# take the whole thing as one directory with one COPY.
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# requirements.txt only — NOT requirements-dev.txt. pytest and its tree have no
# business in a production image.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt


# ---- runtime ----------------------------------------------------------------

FROM python:3.13-slim AS runtime

LABEL org.opencontainers.image.title="gen-portal-api" \
      org.opencontainers.image.description="Client portal API for Genmars Tech Limited" \
      org.opencontainers.image.vendor="Genmars Tech Limited" \
      org.opencontainers.image.source="https://github.com/genmarstech/gen-portal"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    DJANGO_SETTINGS_MODULE=config.settings

# libpq5 is the RUNTIME half of libpq-dev: psycopg needs the shared library, not
# the headers. Installing libpq-dev here instead would drag the compiler back in.
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends libpq5 curl; \
    rm -rf /var/lib/apt/lists/*; \
    groupadd -g 10002 -r app; \
    useradd -u 10002 -r -g app -s /usr/sbin/nologin -d /app app

WORKDIR /app

COPY --from=build /opt/venv /opt/venv
COPY --chown=10002:10002 . .

# collectstatic at BUILD time, not at boot.
#
# Doing it in an entrypoint would mean every restart races to write the same
# files, and a container that cannot write would fail to start for a reason that
# has nothing to do with serving requests. Built once, the directory is read-only
# thereafter and gets bind-mounted out for Caddy to serve (see compose.yaml).
#
# The build has no database and no real secret, so both are stubbed for this one
# command. Nothing here is baked into the image: settings read the environment
# at runtime.
RUN set -eux; \
    DJANGO_SECRET_KEY=build-time-only-not-a-secret \
    DEBUG=True \
    DATABASE_URL=sqlite:///build.sqlite3 \
    python manage.py collectstatic --noinput --clear; \
    rm -f build.sqlite3; \
    chown -R 10002:10002 /app/staticfiles

# ── where uploaded client files land ────────────────────────────────────────
#
# Created HERE, owned by the app user, even though a named volume is mounted
# over it at runtime. Docker seeds an empty named volume from the image path it
# covers — including its ownership — so without this the volume is created
# root-owned, the unprivileged app user cannot write to it, and every upload
# fails with a permission error at the moment a client's photo is being saved.
#
# Nothing here serves this directory. See portal/attachments.py.
RUN mkdir -p /app/media && chown 10002:10002 /app/media

USER 10002:10002

EXPOSE 8010

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8010/api/health || exit 1

# gunicorn, not `manage.py runserver`. runserver is single-threaded, reloads on
# file changes and explicitly says it is not for production.
#
# --access-logfile - sends access logs to stdout so the container log driver
# handles rotation; a file inside the container would grow until the disk filled.
CMD ["gunicorn", "config.wsgi:application", \
     "--bind", "0.0.0.0:8010", \
     "--workers", "3", \
     "--timeout", "30", \
     "--graceful-timeout", "30", \
     "--access-logfile", "-", \
     "--error-logfile", "-", \
     "--forwarded-allow-ips", "*"]
