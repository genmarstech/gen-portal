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
# into a throwaway database and then ASSERTS THE DATA IS THERE: the tables the
# application depends on, and a row count that is not zero. A restore that
# "succeeds" into an empty database is the exact failure this is written to
# catch.
#
# It is safe to run against production. The scratch database is created and
# dropped by this script and is never the one the application uses; the live
# database is only ever read from, never written to.

set -euo pipefail

cd "$(dirname "$0")/.."

DB_SERVICE="${DB_SERVICE:-db}"
POSTGRES_USER="${POSTGRES_USER:-genmars}"
SCRATCH_DB="${SCRATCH_DB:-genmars_restore_check}"
BACKUP_DIR="${BACKUP_DIR:-./backups}"

dump_path="${1:-}"
if [ -z "$dump_path" ]; then
    dump_path="$(find "$BACKUP_DIR" -maxdepth 1 -name 'portal-*.dump' -type f | sort | tail -1)"
fi

if [ -z "$dump_path" ] || [ ! -s "$dump_path" ]; then
    echo "FATAL: no non-empty dump found. Nothing to test." >&2
    exit 1
fi

dump_name="$(basename "$dump_path")"
echo "==> Testing restore of ${dump_name}"

psql_scratch() {
    docker compose exec -T "$DB_SERVICE" \
        psql -U "$POSTGRES_USER" -d "$SCRATCH_DB" -tAc "$1"
}

cleanup() {
    echo "==> Dropping scratch database ${SCRATCH_DB}"
    docker compose exec -T "$DB_SERVICE" \
        dropdb -U "$POSTGRES_USER" --if-exists --force "$SCRATCH_DB" >/dev/null 2>&1 || true
}
# Runs on success, failure and Ctrl-C alike. Without it a failed run leaves a
# scratch database behind, and the next run fails for the wrong reason.
trap cleanup EXIT

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

echo
if [ "$failures" -ne 0 ]; then
    echo "RESTORE TEST FAILED — ${failures} problem(s) with ${dump_name}." >&2
    echo "Treat the backups as unusable until this passes." >&2
    exit 1
fi

echo "RESTORE TEST PASSED — ${dump_name} restores and contains data."
echo "Record the date in docs/DEPLOYMENT.md; Tier 1 asks for a tested restore,"
echo "and 'tested' means recently, not once."
