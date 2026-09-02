#!/usr/bin/env bash
#
# Collect encrypted backups OFF the server. Run this on your own machine.
#
# ═══════════════════════════════════════════════════════════════════════════
# IT PULLS. IT IS NEVER RUN ON THE SERVER, AND THE SERVER NEVER PUSHES.
#
# That direction is the point. A push means the server holds credentials for
# the backup destination — so anything that compromises the server can reach
# the backups and delete them, which is exactly what ransomware does first. A
# pull means the destination reaches in and takes a copy, and the server has no
# way to touch what has already been collected.
#
# It also means backups keep working when the server is on fire, which is the
# only moment they matter.
# ═══════════════════════════════════════════════════════════════════════════
#
#   ./scripts/pull-backups.sh [destination-directory]
#
# Default destination is ~/genmars-backups.
#
# ── WHAT IT COLLECTS ────────────────────────────────────────────────────────
#
# Only ./backups/offsite on the server: the GPG-encrypted copies. The plaintext
# archive beside them stays on the server, where the automated restore test
# uses it. Nothing readable ever crosses the network or lands on this machine.
#
# ── THIS IS HALF THE JOB ────────────────────────────────────────────────────
#
# Collecting files proves nothing about whether they can be opened. Roughly
# monthly, run the restore test against one of them ON THIS MACHINE, where the
# private key lives:
#
#   ./scripts/restore-test.sh ~/genmars-backups/portal-<stamp>.dump.gpg
#
# An encrypted backup nobody has ever opened is a file, not a backup.

set -euo pipefail

REMOTE="${REMOTE:-genmars}"
REMOTE_DIR="${REMOTE_DIR:-/opt/gen-portal/backups/offsite}"
DEST="${1:-$HOME/genmars-backups}"

mkdir -p "$DEST"
# Nobody else on this machine needs to read the company's backups.
chmod 700 "$DEST"

echo "==> Pulling encrypted backups from ${REMOTE}:${REMOTE_DIR}"

# -a preserves timestamps, which is what makes the "how old is the newest copy"
# check below mean anything. No --delete: the server prunes on a 14-day window
# and this archive is meant to outlive that. Copies collected here are ours.
if ! rsync -a --info=stats1 \
       "${REMOTE}:${REMOTE_DIR}/" "${DEST}/" 2>/dev/null; then
    echo "FATAL: could not reach ${REMOTE}. Nothing was collected." >&2
    exit 1
fi

chmod 600 "$DEST"/*.gpg 2>/dev/null || true

count="$(find "$DEST" -maxdepth 1 -name 'portal-*.dump.gpg' -type f | wc -l | tr -d ' ')"
newest="$(find "$DEST" -maxdepth 1 -name 'portal-*.dump.gpg' -type f -printf '%T@ %p\n' 2>/dev/null | sort -n | tail -1 | cut -d' ' -f2-)"

echo "==> ${count} encrypted backup(s) in ${DEST}"

if [ -z "$newest" ]; then
    echo "FATAL: nothing was collected. Either the server has made no encrypted" >&2
    echo "       copies (is BACKUP_RECIPIENT set?) or the pull silently failed." >&2
    exit 1
fi

age_hours=$(( ( $(date +%s) - $(stat -c %Y "$newest") ) / 3600 ))
echo "==> Newest is ${age_hours}h old: $(basename "$newest")"

# A pull that "succeeds" against a directory the server stopped writing to is
# the failure this catches: everything looks fine and the copies quietly stop
# being current.
if [ "$age_hours" -gt 48 ]; then
    echo
    echo "WARNING: the newest copy is more than two days old. The nightly backup" >&2
    echo "         may have stopped, or stopped encrypting. Check the server." >&2
    exit 1
fi

echo
echo "Collected. Now the half that proves it works — roughly monthly, on this"
echo "machine, where the private key is:"
echo "  ./scripts/restore-test.sh ${newest}"
