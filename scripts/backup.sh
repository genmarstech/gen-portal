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

# Encrypted copies wait here to be collected. Separate from ./backups so a
# pull can take the whole directory without also taking the plaintext archive.
OFFSITE_DIR="${OFFSITE_DIR:-./backups/offsite}"

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

# ── OWNERSHIP, THEN PERMISSIONS, AND IN THAT ORDER ──────────────────────────
#
# pg_dump runs inside the container, so the file lands owned by root as the
# container sees it. Mode 600 then makes it unreadable to whoever is running
# this script — which is fine under the systemd timer, because that is root,
# and broken every other time. "Works only when run by root" is the kind of
# thing that is discovered during an incident.
#
# So the file is handed to whoever owns the backups directory on the host, and
# only then locked down. Both are done from inside the container, which is the
# only side that can: it is root there.
owner="$(stat -c '%u:%g' "$BACKUP_DIR")"
docker compose exec -T "$DB_SERVICE" chown "$owner" "/backups/${name}" >/dev/null 2>&1 || true
docker compose exec -T "$DB_SERVICE" chmod 600 "/backups/${name}" >/dev/null 2>&1 || true

# Every dump, not just tonight's. These were mode 644 for the first weeks of
# the company; fixing only new ones would leave the old ones exposed forever.
docker compose exec -T "$DB_SERVICE" \
    sh -c "chown ${owner} /backups/portal-*.dump 2>/dev/null; \
           chmod 600 /backups/portal-*.dump 2>/dev/null" \
    >/dev/null 2>&1 || true

# ── an encrypted COPY, for leaving the building ─────────────────────────────
#
# ══════════════════════════════════════════════════════════════════════════
# WHY THE LOCAL DUMP STAYS IN CLEAR AND ONLY THE COPY IS ENCRYPTED.
#
# The first version of this encrypted in place and deleted the plaintext. That
# is wrong here, and the reason is the restore test.
#
# This host holds the PUBLIC half of the key only — deliberately, so that
# taking the server does not hand over the archive of every earlier state of
# the database. But it means the host cannot decrypt, and therefore cannot run
# an automated weekly restore against an encrypted archive. Encrypting
# everything would have quietly traded a working restore test for a stronger
# threat model, and an unverified backup is not a backup.
#
# So: the local archive stays plaintext at mode 600, where restore-test.sh
# verifies the real bytes every week; and a separate encrypted copy is made for
# anything that leaves this machine.
#
# The trade is honest. Local plaintext only matters to somebody who already has
# this host — and they already have the live database sitting next to it. The
# risk encryption actually addresses is a backup copy in somebody else's
# storage, on a stolen laptop, or in a bucket that turned out to be public.
# That is precisely the copy that is encrypted.
# ══════════════════════════════════════════════════════════════════════════

if [ -n "$BACKUP_RECIPIENT" ]; then
    mkdir -p "$OFFSITE_DIR"
    chmod 700 "$OFFSITE_DIR" 2>/dev/null || true

    echo "==> Encrypting a copy for off-box, to ${BACKUP_RECIPIENT}"
    if ! gpg --batch --yes --trust-model always \
             --recipient "$BACKUP_RECIPIENT" \
             --output "${OFFSITE_DIR}/${name}.gpg" --encrypt "$target"; then
        echo "FATAL: could not encrypt the off-box copy. The local dump is" >&2
        echo "       fine; nothing should leave this host until this works." >&2
        exit 1
    fi

    if [ ! -s "${OFFSITE_DIR}/${name}.gpg" ]; then
        echo "FATAL: gpg produced an empty file." >&2
        rm -f "${OFFSITE_DIR}/${name}.gpg"
        exit 1
    fi
    chmod 600 "${OFFSITE_DIR}/${name}.gpg"

    # ── prove it is addressed to the key we think it is ─────────────────────
    #
    # gpg encrypting "successfully" to the wrong key looks identical to
    # encrypting to the right one until somebody tries to open it. This reads
    # the packet header back and checks the recipient, which is the one part of
    # "can it be decrypted" that a machine without the private key CAN check.
    # The output is captured first and the EXIT STATUS ignored on purpose.
    # `gpg --list-packets` also attempts a decrypt, so on this host it always
    # ends with "No secret key" and exits non-zero — which is the correct state
    # here, not a fault. Piping it straight into grep under `set -o pipefail`
    # turned that expected failure into a fatal one on every single run.
    packets="$(gpg --batch --list-packets "${OFFSITE_DIR}/${name}.gpg" 2>/dev/null || true)"
    if ! printf '%s' "$packets" | grep -qi "keyid ${BACKUP_RECIPIENT}"; then
        echo "FATAL: the encrypted copy is not addressed to ${BACKUP_RECIPIENT}." >&2
        rm -f "${OFFSITE_DIR}/${name}.gpg"
        exit 1
    fi

    echo "==> Off-box copy ready: ${OFFSITE_DIR}/${name}.gpg"
fi

size="$(du -h "$target" | cut -f1)"
echo "==> Wrote ${name} (${size})"

# ── retention ───────────────────────────────────────────────────────────────
# Deletes only files matching our own naming pattern, so an unrelated file
# someone parked in this directory is never removed by a routine job.
echo "==> Pruning dumps older than ${RETENTION_DAYS} days"
find "$BACKUP_DIR" -maxdepth 1 -name 'portal-*.dump' -type f \
     -mtime "+${RETENTION_DAYS}" -print -delete

# The encrypted copies are pruned on the SAME window. They are the ones that
# leave, so letting them accumulate here would slowly build a second, larger
# archive of everything — on the same disk, which is the problem they exist to
# solve.
if [ -d "$OFFSITE_DIR" ]; then
    find "$OFFSITE_DIR" -maxdepth 1 -name 'portal-*.dump.gpg' -type f \
         -mtime "+${RETENTION_DAYS}" -print -delete
fi

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
if [ -z "$BACKUP_RECIPIENT" ]; then
    echo "WARNING: BACKUP_RECIPIENT is not set, so no encrypted copy was made"
    echo "         and there is nothing for an off-box pull to collect. Every"
    echo "         dump is on the same disk as the database it came from."
else
    offsite_count="$(find "$OFFSITE_DIR" -maxdepth 1 -name 'portal-*.dump.gpg' -type f 2>/dev/null | wc -l | tr -d ' ')"
    echo "==> ${offsite_count} encrypted copy/copies waiting in ${OFFSITE_DIR}"
    echo
    echo "NOTE: making the copy is not moving it. Until something collects from"
    echo "      ${OFFSITE_DIR}, everything is still on one disk —"
    echo "      see scripts/pull-backups.sh."
fi
