# syntax=docker/dockerfile:1.7

# ─────────────────────────────────────────────────────────────────────────────
# Genmars client portal — Next.js frontend
#
# Unlike the marketing site, this is NOT a static export. The portal needs a
# server: sessions, redirects that depend on who is signed in, and a proxy hop
# to Django. That is the deliberate cost of this application and the reason it
# lives in its own repo rather than inside gen-website, which keeps its
# no-Node runtime image.
#
# `output: "standalone"` (next.config.ts) means the runtime stage copies a
# traced, self-contained server instead of the full node_modules tree. The build
# toolchain — npm, the compiler, the dev dependencies — never reaches production.
# ─────────────────────────────────────────────────────────────────────────────


# ---- dependencies -----------------------------------------------------------

FROM node:22-alpine AS deps

WORKDIR /app

ENV NEXT_TELEMETRY_DISABLED=1 \
    CI=true

COPY package.json package-lock.json ./
RUN --mount=type=cache,target=/root/.npm \
    npm ci --no-audit --no-fund


# ---- build ------------------------------------------------------------------

FROM node:22-alpine AS build

# Where Next's /api/* rewrite points.
#
# THIS IS A BUILD ARG, NOT A RUNTIME VARIABLE, and it cannot be turned into one.
# next.config.ts is evaluated by `next build`, which writes the resolved
# destination into .next/routes-manifest.json; the standalone server then reads
# the manifest and never looks at the environment again. Setting API_ORIGIN
# only in compose looks correct, is visible in `docker compose exec web env`,
# and does nothing at all — the image keeps whatever default was baked in, and
# every /api/* call 500s with ECONNREFUSED to a port inside this container.
#
# The default is the compose service name because that is where this image is
# built to run. Local development outside compose overrides it.
ARG API_ORIGIN=http://api:8010
ENV API_ORIGIN=${API_ORIGIN}

WORKDIR /app

ENV NEXT_TELEMETRY_DISABLED=1 \
    CI=true \
    NODE_ENV=production

COPY --from=deps /app/node_modules ./node_modules
COPY . .

# The verify script is the pipeline, not a nicety: check:theme catches a brand
# constant used as a themeable value — the bug that already shipped once on the
# marketing site — and it is far cheaper to fail here than to find it in dark
# mode in front of a client.
RUN npm run check:theme && npm run build


# ---- runtime ----------------------------------------------------------------

FROM node:22-alpine AS runtime

LABEL org.opencontainers.image.title="gen-portal-web" \
      org.opencontainers.image.description="Client portal frontend for Genmars Tech Limited" \
      org.opencontainers.image.vendor="Genmars Tech Limited" \
      org.opencontainers.image.source="https://github.com/genmarstech/gen-portal"

WORKDIR /app

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3010 \
    HOSTNAME=0.0.0.0

RUN addgroup -g 10003 -S web && adduser -u 10003 -S web -G web

# The standalone bundle carries its own minimal node_modules. static/ and
# public/ are not included in it and have to be copied alongside, or the app
# serves HTML with every asset 404ing — which looks like a broken deploy rather
# than a missing COPY.
COPY --from=build --chown=10003:10003 /app/.next/standalone ./
COPY --from=build --chown=10003:10003 /app/.next/static ./.next/static
COPY --from=build --chown=10003:10003 /app/public ./public

USER 10003:10003

EXPOSE 3010

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD wget -q -O /dev/null http://127.0.0.1:3010/healthz || exit 1

CMD ["node", "server.js"]
