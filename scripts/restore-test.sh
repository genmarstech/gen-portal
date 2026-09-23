#!/usr/bin/env bash
#
# Prove the newest backup can actually be restored.
#
#   ./scripts/restore-test.sh                 # newest dump in ./backups
#   ./scripts/restore-test.sh backups/x.dump  # a specific one
#
# ── WHY THIS SCRIPT EXISTS ──────────────────────────────────────────────────
# Charter 03 §IV Tier 1 requires "automated backup with a TESTED restore". The
# tested half is the half that gets skipped, and skipping it is how teams
# discover — during the incident — that the dumps have been zero bytes for
# months, or were taken against the wrong database, or restore into a schema the
# current code cannot read.
#
# So this does not merely run pg_restore and check the exit code. It restores
# into a throwaway database and then ASSERTS THE DATA IS THERE, in three steps
# that each catch a different lie:
#
#   1. every table the application cannot run without is present — catches a
#      dump taken against a stale or wrong schema;
#   2. users and migration history are non-empty — catches the schema-only
#      restore that reports success into an empty database;
#   3. for contracts, invoices and payments, the restored row counts match what
#      was live when the dump began — catches a PARTIAL restore, which the
#      first two steps would happily wave through.
#
# It is safe to run against production. The scratch database is created and
# dropped by this script and is never the one the application uses; the live
# database is only ever read from, never written to.

set -euo pipefail

cd "$(dirname "$0")/.."

DB_SERVICE="${DB_SERVICE:-db}"
POSTGRES_USER="${POSTGRES_USER:-genmars}"
POSTGRES_DB="${POSTGRES_DB:-genmars_portal}"
SCRATCH_DB="${SCRATCH_DB:-genmars_restore_check}"
BACKUP_DIR="${BACKUP_DIR:-./backups}"

dump_path="${1:-}"
if [ -z "$dump_path" ]; then
    dump_path="$(find "$BACKUP_DIR" -maxdepth 1 \
        \( -name 'portal-*.dump' -o -name 'portal-*.dump.gpg' \) \
        -type f | sort | tail -1)"
fi

if [ -z "$dump_path" ] || [ ! -s "$dump_path" ]; then
    echo "FATAL: no non-empty dump found. Nothing to test." >&2
    exit 1
fi

dump_name="$(basename "$dump_path")"
echo "==> Testing restore of ${dump_name}"

# ── DECRYPTING, WHEN THERE IS SOMETHING TO DECRYPT ──────────────────────────
#
# The nightly archive on the server is plaintext, so the scheduled run never
# reaches this branch — the server holds only the public half of the key and
# could not decrypt anyway. See the long note in backup.sh for why it is
# arranged that way.
#
# This exists for the drill that matters: running this script on the machine
# that HOLDS the private key, against a collected .gpg file. That is the only
# thing that proves an off-box backup can actually be opened, and it is a
# failure with no symptoms — gpg encrypts happily to a key whose private half
# was lost months ago, and every copy since is unreadable with nothing saying
# so.
#
#   ./scripts/restore-test.sh /path/to/portal-<stamp>.dump.gpg
decrypted=""
cleanup_plaintext() {
    if [ -n "$decrypted" ]; then
        rm -f "$decrypted"
        docker compose exec -T "$DB_SERVICE" rm -f "/backups/$(basename "$decrypted")" \
            >/dev/null 2>&1 || true
    fi
}
trap cleanup_plaintext EXIT

case "$dump_name" in
*.gpg)
    echo "==> Decrypting (this is the half of the test that proves the key works)"

    # The directory may not exist. On this server it always does, because
    # backup.sh made it; on the laptop where this drill is actually run, a
    # fresh clone has no ./backups and it is gitignored.
    #
    # business-os hit exactly this and gpg failed with "No such file or
    # directory", whereupon the handler below offered three causes, none of
    # which was the real one — two send you to retype a passphrase that was
    # fine, and the third announces that every backup is unreadable. Crying
    # wolf in the one message that must never cry wolf.
    mkdir -p "$BACKUP_DIR"
    chmod 700 "$BACKUP_DIR" 2>/dev/null || true

    decrypted="${BACKUP_DIR}/.restore-check-$$.dump"
    # NO --batch. The private key is passphrase-protected, and --batch tells
    # gpg never to prompt — so the drill this branch exists for failed with
    # "No passphrase given" and a message announcing the key was lost. It was
    # not; nobody had been asked for it. Without --batch, gpg uses the agent,
    # prompting once and caching for the rest of the session.
    #
    # ── gpg's OWN WORDS ARE CAPTURED AND THEN READ ──────────────────────────
    #
    # Branching on them is the difference between naming the cause and listing
    # suspects. The list below has already been wrong once, in business-os: it
    # blamed a passphrase and then a lost key for what was a missing
    # directory. Its third suspect is "every backup you have is unreadable" —
    # a sentence to spend only when it is true, or it will not be believed on
    # the day it is.
    #
    # pinentry prompts on the TTY, not stderr, so capturing stderr does not
    # swallow the passphrase prompt.
    if ! gpg_message="$(gpg --yes --quiet --output "$decrypted" --decrypt "$dump_path" 2>&1)"; then
        [ -n "$gpg_message" ] && printf '%s\n' "$gpg_message" >&2
        echo >&2
        echo "FATAL: could not decrypt ${dump_name}." >&2
        echo >&2

        case "$gpg_message" in
        *Timeout*|*"Inappropriate ioctl"*|*"no terminal"*|*"No pinentry"*)
            # THE CASE THAT LOOKS LIKE A LOST KEY AND IS NOT.
            #
            # gpg could not ASK for the passphrase. That happens whenever this
            # runs without a terminal — cron, a systemd unit, an editor task,
            # an agent — and also when the agent's own prompt expired while
            # nobody was looking.
            #
            # Nothing is wrong with the key or the file. Reading this as
            # "wrong passphrase" sends somebody to retype a correct one;
            # reading it as "the key is gone" starts an incident that is not
            # happening.
            echo "  gpg could not ASK for your passphrase — it timed out or had" >&2
            echo "  no terminal to prompt on." >&2
            echo >&2
            echo "  NOTHING IS WRONG WITH THE KEY OR THE BACKUP. Run this again" >&2
            echo "  in a terminal you are sitting at:" >&2
            echo "    cd $(pwd) && ${0} ${dump_path}" >&2
            echo >&2
            echo "  If you need it unattended, the passphrase has to reach the" >&2
            echo "  agent some other way — a decision about where that" >&2
            echo "  passphrase then lives, not a flag to add here." >&2
            ;;
        *"No secret key"*)
            echo "  This machine does not hold the private key for this file." >&2
            echo >&2
            echo "  Check which keys are here:" >&2
            echo "    gpg --list-secret-keys" >&2
            echo >&2
            echo "  If it is simply the wrong machine, that is all this is. If" >&2
            echo "  the key existed here and no longer does, that is the" >&2
            echo "  emergency: every backup since encryption was switched on" >&2
            echo "  is unreadable." >&2
            ;;
        *"Bad passphrase"*|*"bad passphrase"*)
            echo "  Wrong passphrase. Try again — this is the ordinary one." >&2
            ;;
        *)
            # Ordered by likelihood, not by drama. Only the last is the
            # emergency.
            echo "  1. Wrong or unentered passphrase — try again." >&2
            echo "  2. This machine does not hold the private key. Check with:" >&2
            echo "       gpg --list-secret-keys ${BACKUP_RECIPIENT:-413CB8DF5FECF5F4}" >&2
            echo "  3. If the key is genuinely gone, THAT is the emergency:" >&2
            echo "     every backup since encryption was switched on is" >&2
            echo "     unreadable." >&2
            ;;
        esac
        exit 1
    fi
    chmod 600 "$decrypted"

    # ── gpg EXITING 0 IS NOT PROOF OF A USABLE DUMP ─────────────────────────
    # It proves bytes came out. A custom-format dump begins with the five
    # characters PGDMP, so this is the cheapest check that what came out is a
    # dump rather than a truncated file — and it is the whole of what the
    # laptop drill can prove without a database.
    if [ "$(head -c 5 "$decrypted")" != "PGDMP" ]; then
        echo "FATAL: decrypted, but the result is not a Postgres custom-format" >&2
        echo "       dump. The key works; the FILE is wrong." >&2
        exit 1
    fi
    echo "    ok       the key opens it, and the result is a real pg_dump"

    dump_name="$(basename "$decrypted")"
    ;;
esac

# ── IS THERE A DATABASE TO RESTORE INTO ─────────────────────────────────────
#
# On this server there always is. On the laptop — the ONLY machine that can
# decrypt, and therefore the only place the .gpg drill can happen — there
# usually is not.
#
# This script assumed one and failed deep inside pg_restore with a docker
# error, which reads as "the backup is broken" and is not. So it is checked up
# front, and a missing database ENDS THE RUN SUCCESSFULLY when what was being
# tested is an encrypted copy: the key opening the file is exactly what that
# drill exists to prove, and the schema and data assertions already run weekly
# here against the plaintext archive.
#
# A drill awkward enough to fail for the wrong reason is a drill that stops
# being done, which is how an unopenable archive goes unnoticed for a year.
if ! docker compose ps --format '{{.Service}}' 2>/dev/null | grep -qx "$DB_SERVICE"; then
    echo
    if [ -n "$decrypted" ]; then
        echo "KEY TEST PASSED — ${dump_name} decrypts to a valid dump."
        echo
        echo "Not run: the schema and data assertions, which need a database."
        echo "There is no '${DB_SERVICE}' service up here. That is expected on a"
        echo "laptop, and those assertions run weekly on the server against the"
        echo "plaintext archive — see genmars-portal-restore-test.timer."
        echo
        echo "What this DID prove is the half the server cannot: that the"
        echo "private key still opens what the server has been encrypting."
        exit 0
    fi
    echo "FATAL: no '${DB_SERVICE}' service is running, so there is nowhere to" >&2
    echo "       restore into. Start the stack, or run this on the server." >&2
    exit 1
fi

psql_scratch() {
    docker compose exec -T "$DB_SERVICE" \
        psql -U "$POSTGRES_USER" -d "$SCRATCH_DB" -tAc "$1"
}

# Read-only, and only ever used for counting. The live database is never
# written to by this script.
psql_live() {
    docker compose exec -T "$DB_SERVICE" \
        psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "$1"
}

cleanup() {
    echo "==> Dropping scratch database ${SCRATCH_DB}"
    docker compose exec -T "$DB_SERVICE" \
        dropdb -U "$POSTGRES_USER" --if-exists --force "$SCRATCH_DB" >/dev/null 2>&1 || true
}
# Runs on success, failure and Ctrl-C alike. Without it a failed run leaves a
# scratch database behind, and the next run fails for the wrong reason.
#
# Chained with cleanup_plaintext, set earlier: bash keeps only ONE EXIT trap, so
# replacing it here would leave a decrypted copy of the whole database sitting
# in the backups directory after every run.
trap 'cleanup; cleanup_plaintext' EXIT

cleanup
echo "==> Creating scratch database ${SCRATCH_DB}"
docker compose exec -T "$DB_SERVICE" createdb -U "$POSTGRES_USER" "$SCRATCH_DB"

echo "==> Restoring"
# --exit-on-error matters. Without it pg_restore reports individual failures and
# still exits 0, so a restore missing half its tables looks like a success.
docker compose exec -T "$DB_SERVICE" \
    pg_restore -U "$POSTGRES_USER" -d "$SCRATCH_DB" --no-owner --exit-on-error \
    "/backups/${dump_name}"

# ── the assertions ──────────────────────────────────────────────────────────

echo "==> Checking the restored schema"

failures=0

require_table() {
    local table="$1"
    local present
    present="$(psql_scratch "SELECT to_regclass('public.${table}') IS NOT NULL;")"
    if [ "$present" = "t" ]; then
        echo "    ok       ${table}"
    else
        echo "    MISSING  ${table}" >&2
        failures=$((failures + 1))
    fi
}

# Every table the application cannot function without. Losing any one of these
# means the restore is not usable, whatever pg_restore reported.
require_table accounts_user
require_table accounts_organisation
require_table accounts_membership
require_table accounts_emailcode
require_table portal_order
require_table portal_progressnote
require_table portal_milestone
require_table portal_enquiry
require_table portal_service
require_table portal_contract
require_table portal_invoice
require_table portal_mpesapayment
require_table portal_paymentrecord
require_table portal_notification
require_table portal_deliverygate
require_table portal_blocker
require_table django_migrations

echo "==> Checking the restored data"

users="$(psql_scratch 'SELECT count(*) FROM accounts_user;')"
migrations="$(psql_scratch 'SELECT count(*) FROM django_migrations;')"

echo "    accounts_user rows:     ${users}"
echo "    django_migrations rows: ${migrations}"

# An empty user table means the dump captured a schema and no content — which is
# precisely the "successful" restore that is worthless in an incident.
if [ "${users:-0}" -lt 1 ]; then
    echo "    FAIL: no users restored. A schema-only restore is not a backup." >&2
    failures=$((failures + 1))
fi

if [ "${migrations:-0}" -lt 1 ]; then
    echo "    FAIL: no migration history. The schema will not match the code." >&2
    failures=$((failures + 1))
fi

# ── content completeness ────────────────────────────────────────────────────
# The checks above prove the restore is not empty. They do not prove it is
# COMPLETE, and for the tables that carry contracts and money "not empty" is a
# long way from good enough: a dump that captured three of nine invoices would
# sail through everything above.
#
# So compare counts. pg_dump takes its snapshot when it starts, and the dump
# filename carries that moment in UTC, so every row created before it must be
# present in the restore. Rows created afterwards are legitimately absent and
# are excluded from the live count rather than treated as loss.
#
# Restored > expected is not a failure: it means rows were deleted from live
# after the dump was taken, which is the backup doing its job. It is still
# worth printing, because on a table nothing should ever delete from it is
# worth a human's attention.

# portal-20260902-021758.dump -> 2026-09-02 02:17:58+00
# Read from the ORIGINAL filename, not the decrypted temporary one, which
# carries a process id instead of a timestamp.
stamp="$(basename "$dump_path" | sed -nE 's/^portal-([0-9]{4})([0-9]{2})([0-9]{2})-([0-9]{2})([0-9]{2})([0-9]{2})\.dump(\.gpg)?$/\1-\2-\3 \4:\5:\6+00/p')"

if [ -z "$stamp" ]; then
    echo "    note: ${dump_name} is not a scheduled dump name; skipping the"
    echo "          count comparison, which needs the dump's timestamp."
else
    echo "==> Comparing row counts against the live database (as at ${stamp})"

    compare_counts() {
        local table="$1"
        local expected restored

        # A table missing from the restore is already recorded as a failure by
        # require_table. Counting it here would abort the run on a raw psql
        # error and hide the remaining comparisons, so skip it and let the
        # earlier, clearer failure stand.
        if [ "$(psql_scratch "SELECT to_regclass('public.${table}') IS NOT NULL;")" != "t" ]; then
            echo "    skipped  ${table}: not in this dump (see MISSING above)"
            return
        fi

        expected="$(psql_live "SELECT count(*) FROM ${table} WHERE created_at < '${stamp}';")"
        restored="$(psql_scratch "SELECT count(*) FROM ${table};")"

        if [ "${restored:-0}" -lt "${expected:-0}" ]; then
            echo "    LOST     ${table}: ${restored} restored, ${expected} expected" >&2
            failures=$((failures + 1))
        elif [ "${restored:-0}" -gt "${expected:-0}" ]; then
            echo "    ok       ${table}: ${restored} restored (${expected} live now — rows deleted since)"
        else
            echo "    ok       ${table}: ${restored}"
        fi
    }

    # Only tables with a created_at, and only the ones where losing a row is a
    # business problem rather than an inconvenience. Sessions and admin logs are
    # deliberately absent: they regenerate, and they churn enough to make this
    # check noisy for no gain.
    compare_counts portal_service
    compare_counts portal_contract
    compare_counts portal_invoice
    compare_counts portal_mpesapayment
    # The payment ledger. Every shilling the company has been paid is a row
    # here now, so a dump that loses these loses the record of the money.
    compare_counts portal_paymentrecord
    compare_counts portal_enquiry
fi

echo
if [ "$failures" -ne 0 ]; then
    echo "RESTORE TEST FAILED — ${failures} problem(s) with ${dump_name}." >&2
    echo "Treat the backups as unusable until this passes." >&2
    exit 1
fi

echo "RESTORE TEST PASSED — ${dump_name} restores and contains data."
echo "Record the date in docs/DEPLOYMENT.md; Tier 1 asks for a tested restore,"
echo "and 'tested' means recently, not once."
