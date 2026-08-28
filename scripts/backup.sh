#!/usr/bin/env bash
#
# Postgres backup for the Genmars client portal.
#
#   ./scripts/backup.sh
#
# Run from the repository root on the host, or via the systemd timer in
# deploy/. Writes a compressed custom-format dump to ./backups/ and prunes
# anything older than the retention window.
#
# ── WHY CUSTOM FORMAT ───────────────────────────────────────────────────────
# `pg_dump -Fc` rather than plain SQL: it is compressed, and pg_restore can read
# it selectively. It also cannot be "restored" by accidentally piping it into
# psql against the live database, which a .sql file invites.
#
# ── THIS SCRIPT IS HALF OF THE JOB ──────────────────────────────────────────
# A backup that has never been restored is not a backup — it is a file. The
# other half is scripts/restore-test.sh, which restores the newest dump into a
# scratch database and checks the data is actually there. Charter 03 §IV Tier 1
# requires the tested restore, not the dump.

set -euo pipefail

cd "$(dirname "$0")/.."

RETENTION_DAYS="${RETENTION_DAYS:-14}"
BACKUP_DIR="${BACKUP_DIR:-./backups}"
DB_SERVICE="${DB_SERVICE:-db}"
POSTGRES_USER="${POSTGRES_USER:-genmars}"
POSTGRES_DB="${POSTGRES_DB:-genmars_portal}"

mkdir -p "$BACKUP_DIR"

stamp="$(date -u +%Y%m%d-%H%M%S)"
name="portal-${stamp}.dump"
target="${BACKUP_DIR}/${name}"

echo "==> Dumping ${POSTGRES_DB} to ${target}"

# The dump is written INSIDE the container to /backups, which is bind-mounted to
# ./backups on the host. Streaming through stdout would work too, but a broken
# pipe mid-transfer leaves a truncated file that looks complete — writing to the
# mount and checking the exit status does not.
docker compose exec -T "$DB_SERVICE" \
    pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc -f "/backups/${name}"

if [ ! -s "$target" ]; then
    echo "FATAL: ${target} is missing or empty. The backup did NOT succeed." >&2
    exit 1
fi

size="$(du -h "$target" | cut -f1)"
echo "==> Wrote ${name} (${size})"

# ── retention ───────────────────────────────────────────────────────────────
# Deletes only files matching our own naming pattern, so an unrelated file
# someone parked in this directory is never removed by a routine job.
echo "==> Pruning dumps older than ${RETENTION_DAYS} days"
find "$BACKUP_DIR" -maxdepth 1 -name 'portal-*.dump' -type f -mtime "+${RETENTION_DAYS}" -print -delete

count="$(find "$BACKUP_DIR" -maxdepth 1 -name 'portal-*.dump' -type f | wc -l | tr -d ' ')"
echo "==> ${count} dump(s) retained in ${BACKUP_DIR}"

# ── OFF-BOX COPY ────────────────────────────────────────────────────────────
# NOT IMPLEMENTED, and its absence is the current weakest link: every dump lives
# on the same disk as the database it came from, so one failed volume loses
# both. This needs a destination decision (object storage, or another host) and
# a credential with append-only permissions — a backup an attacker can delete is
# a backup an attacker will delete.
#
# Tracked in docs/PRE-LAUNCH.md. Do not describe backups as complete until this
# exists.
echo
echo "NOTE: dumps are on the same host as the database. Off-box copy is not yet"
echo "      configured — see docs/PRE-LAUNCH.md."
