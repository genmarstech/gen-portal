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
# Only the ./backups/offsite directories on the server: the GPG-encrypted
# copies. The plaintext archives beside them stay there, where the automated
# restore tests use them. Nothing readable ever crosses the network or lands on
# this machine.
#
# ── IT COLLECTS FROM BOTH APPLICATIONS ──────────────────────────────────────
#
# gen-portal and business-os are separate databases on one host, and for a
# while this script knew about only the first. business-os is the one holding
# OTHER BUSINESSES' takings, and its encrypted copies sat on the server
# uncollected because nothing came for them.
#
# Both land in the SAME directory, flat. That is deliberate and not laziness:
# the filenames are already prefixed — portal-<stamp>.dump.gpg and
# business-<stamp>.dump.gpg — so they cannot collide, and every path already
# written down in a runbook keeps working. Sorting them into subdirectories
# would have been tidier and would have quietly invalidated the one command
# somebody might need to run in a hurry.
#
# ── THIS IS HALF THE JOB ────────────────────────────────────────────────────
#
# Collecting files proves nothing about whether they can be opened. Roughly
# monthly, run the restore test against one of each ON THIS MACHINE, where the
# private key lives:
#
#   gen-portal/scripts/restore-test.sh ~/genmars-backups/portal-<stamp>.dump.gpg
#   business-os/scripts/restore-test.sh ~/genmars-backups/business-<stamp>.dump.gpg
#
# Each repository's own restore test knows its own tables, so they are not
# interchangeable. An encrypted backup nobody has ever opened is a file, not a
# backup.

set -euo pipefail

REMOTE="${REMOTE:-genmars}"
DEST="${1:-$HOME/genmars-backups}"

# name | remote offsite directory | filename prefix
#
# Add a row when a fourth application starts taking backups. The prefix must
# match what that application's backup.sh writes, or the count and age checks
# below silently pass on zero files.
SOURCES=(
    "gen-portal|/opt/gen-portal/backups/offsite|portal"
    "business-os|/opt/business-os/backups/offsite|business"
)

mkdir -p "$DEST"
# Nobody else on this machine needs to read the company's backups.
chmod 700 "$DEST"

# Every source is attempted even when an earlier one fails, and the script
# exits non-zero if ANY did. Stopping at the first failure would mean a broken
# gen-portal pull silently prevented business-os being collected at all —
# one fault becoming two.
problems=0

for source in "${SOURCES[@]}"; do
    IFS='|' read -r name remote_dir prefix <<< "$source"

    echo
    echo "════ ${name} ════"
    echo "==> Pulling encrypted backups from ${REMOTE}:${remote_dir}"

    # -a preserves timestamps, which is what makes the "how old is the newest
    # copy" check below mean anything. No --delete: the server prunes on a
    # 14-day window and this archive is meant to outlive that. Copies collected
    # here are ours.
    #
    # rsync's stderr is NOT discarded, and the exit code is NOT collapsed to
    # "could not reach".
    #
    # It used to be both, and that is how this went unnoticed for three days:
    # the nightly systemd backup runs as root and writes 0600 root:root, this
    # pull runs as an ordinary user over SSH, and rsync could not open a single
    # one of them. It exited 23 — "some files were not transferred" — having
    # successfully copied the manual backups either side of them. The old code
    # threw the reason away and printed "could not reach genmars", which sends
    # whoever reads it to look at the network while every scheduled backup
    # quietly stays on one disk.
    #
    # 23 is the code that matters here. It means the connection was fine and
    # something was skipped, which for a backup pull is worse than a clean
    # failure, because the directory afterwards looks populated.
    set +e
    rsync -a --info=stats1 "${REMOTE}:${remote_dir}/" "${DEST}/"
    rsync_status=$?
    set -e

    if [ "$rsync_status" -eq 23 ] || [ "$rsync_status" -eq 24 ]; then
        echo >&2
        echo "FAILED (${name}): rsync could not collect every file (exit ${rsync_status})." >&2
        echo "       The errors above name them. A backup this pull cannot READ" >&2
        echo "       is a backup that has never left the server." >&2
        echo >&2
        echo "       The usual cause is ownership: the nightly systemd job runs" >&2
        echo "       as root and writes 0600 root:root, and this pull does not" >&2
        echo "       run as root. Check with:" >&2
        echo "         ssh ${REMOTE} ls -la ${remote_dir}" >&2
        problems=$((problems + 1))
        continue
    elif [ "$rsync_status" -ne 0 ]; then
        echo "FAILED (${name}): could not reach ${REMOTE}:${remote_dir} (exit ${rsync_status})." >&2
        problems=$((problems + 1))
        continue
    fi

    chmod 600 "$DEST"/${prefix}-*.dump.gpg 2>/dev/null || true

    count="$(find "$DEST" -maxdepth 1 -name "${prefix}-*.dump.gpg" -type f | wc -l | tr -d ' ')"
    newest="$(find "$DEST" -maxdepth 1 -name "${prefix}-*.dump.gpg" -type f -printf '%T@ %p\n' 2>/dev/null | sort -n | tail -1 | cut -d' ' -f2-)"

    echo "==> ${count} encrypted backup(s) for ${name} in ${DEST}"

    if [ -z "$newest" ]; then
        echo "FAILED (${name}): nothing was collected. Either the server has made" >&2
        echo "       no encrypted copies (is BACKUP_RECIPIENT set in its .env?)" >&2
        echo "       or the pull silently failed." >&2
        problems=$((problems + 1))
        continue
    fi

    age_hours=$(( ( $(date +%s) - $(stat -c %Y "$newest") ) / 3600 ))
    echo "==> Newest is ${age_hours}h old: $(basename "$newest")"

    # A pull that "succeeds" against a directory the server stopped writing to
    # is the failure this catches: everything looks fine and the copies quietly
    # stop being current.
    if [ "$age_hours" -gt 48 ]; then
        echo "WARNING (${name}): the newest copy is more than two days old. The" >&2
        echo "         nightly backup may have stopped, or stopped encrypting." >&2
        echo "         Check: ssh ${REMOTE} systemctl list-timers | grep backup" >&2
        problems=$((problems + 1))
    fi
done

echo
if [ "$problems" -ne 0 ]; then
    echo "${problems} source(s) had a problem. Read the messages above — a" >&2
    echo "directory that still looks populated is the whole danger here." >&2
    exit 1
fi

echo "Collected from every source. Now the half that proves it works — roughly"
echo "monthly, on this machine, where the private key is. Each application has"
echo "its own restore test because each knows its own tables:"
echo
echo "  gen-portal/scripts/restore-test.sh \\"
echo "    $(find "$DEST" -maxdepth 1 -name 'portal-*.dump.gpg' -type f -printf '%T@ %p\n' 2>/dev/null | sort -n | tail -1 | cut -d' ' -f2-)"
echo "  business-os/scripts/restore-test.sh \\"
echo "    $(find "$DEST" -maxdepth 1 -name 'business-*.dump.gpg' -type f -printf '%T@ %p\n' 2>/dev/null | sort -n | tail -1 | cut -d' ' -f2-)"
