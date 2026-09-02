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

# ── ENCRYPTION KEY ──────────────────────────────────────────────────────────
#
# A PUBLIC key, and that asymmetry is the entire point. This host can encrypt a
# backup and cannot decrypt one — so somebody who takes the server takes the
# live database (which they already had) and NOT the archive of every earlier
# state of it. A passphrase stored in .env beside the dumps would protect
# against a stolen file and against nothing else.
#
# Unset means unencrypted, loudly. See the warning at the end of this script.
BACKUP_RECIPIENT="${BACKUP_RECIPIENT:-}"
GNUPGHOME="${GNUPGHOME:-/opt/gen-portal/.gnupg}"
export GNUPGHOME

mkdir -p "$BACKUP_DIR"

# ── PERMISSIONS ─────────────────────────────────────────────────────────────
#
# These files are every client's contracts, invoices, payment records and
# support threads. They were mode 644 on a shared host, which meant any process
# running as any user could read all of it.
#
# Set on every run rather than once by hand: a permission fixed manually is a
# permission that comes back wrong the next time the directory is recreated.
#
# The directory is ours on the host. The dump FILES are not — pg_dump runs
# inside the container and they land owned by its uid, so a host-side chmod
# fails whenever this script is run by anything other than root. They are
# chmodded from inside the container instead, where the owner is doing it.
chmod 700 "$BACKUP_DIR" 2>/dev/null || true

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

# Inside the container: the process that created it is the one that owns it.
# `|| true` so a permission problem is loud in the listing rather than fatal —
# an unreadable backup is worse than a readable one.
docker compose exec -T "$DB_SERVICE" chmod 600 "/backups/${name}" >/dev/null 2>&1 || true

# Every dump, not just tonight's. These were mode 644 for the first weeks of
# the company; fixing only new ones would leave the old ones exposed forever.
docker compose exec -T "$DB_SERVICE" \
    sh -c 'chmod 600 /backups/portal-*.dump /backups/portal-*.dump.gpg 2>/dev/null' \
    >/dev/null 2>&1 || true

# ── encrypt, if we have somewhere to encrypt to ─────────────────────────────
#
# The plaintext is removed only after gpg reports success AND the ciphertext
# exists and is non-empty. Deleting first would turn one bad gpg invocation
# into a night with no backup at all.
if [ -n "$BACKUP_RECIPIENT" ]; then
    echo "==> Encrypting to ${BACKUP_RECIPIENT}"
    if gpg --batch --yes --trust-model always \
           --recipient "$BACKUP_RECIPIENT" \
           --output "${target}.gpg" --encrypt "$target"; then
        if [ -s "${target}.gpg" ]; then
            docker compose exec -T "$DB_SERVICE" \
                chmod 600 "/backups/${name}.gpg" >/dev/null 2>&1 || true
            rm -f "$target" 2>/dev/null || \
                docker compose exec -T "$DB_SERVICE" rm -f "/backups/${name}" \
                >/dev/null 2>&1
            target="${target}.gpg"
            name="${name}.gpg"
        else
            echo "FATAL: gpg produced an empty file. Keeping the plaintext dump." >&2
            rm -f "${target}.gpg"
            exit 1
        fi
    else
        echo "FATAL: encryption failed. Keeping the plaintext dump so tonight" >&2
        echo "       still has a backup, but fix this — it is sitting in clear." >&2
        exit 1
    fi
fi

size="$(du -h "$target" | cut -f1)"
echo "==> Wrote ${name} (${size})"

# ── retention ───────────────────────────────────────────────────────────────
# Deletes only files matching our own naming pattern, so an unrelated file
# someone parked in this directory is never removed by a routine job.
echo "==> Pruning dumps older than ${RETENTION_DAYS} days"
find "$BACKUP_DIR" -maxdepth 1 \( -name 'portal-*.dump' -o -name 'portal-*.dump.gpg' \) \
     -type f -mtime "+${RETENTION_DAYS}" -print -delete

count="$(find "$BACKUP_DIR" -maxdepth 1 \( -name 'portal-*.dump' -o -name 'portal-*.dump.gpg' \) -type f | wc -l | tr -d ' ')"
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
if [ -z "$BACKUP_RECIPIENT" ]; then
    echo "WARNING: BACKUP_RECIPIENT is not set, so these dumps are in CLEAR TEXT."
    echo "         They contain every client's contracts, invoices, payment"
    echo "         records and support threads. See docs/DEPLOYMENT.md."
fi
echo "NOTE: dumps are on the same host as the database. Off-box copy is not yet"
echo "      configured — see docs/PRE-LAUNCH.md."
