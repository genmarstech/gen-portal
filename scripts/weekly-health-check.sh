#!/usr/bin/env bash
#
# Does the backup and health infrastructure actually work, this week?
#
#   ./scripts/weekly-health-check.sh
#
# ═══════════════════════════════════════════════════════════════════════════
# WHY THIS EXISTS BESIDE THE MAIL-ON-FAILURE ALERTS.
#
# Every backup and restore-test timer already has an OnFailure= unit wired to
# notify.sh, so a crash reaches somebody. That catches "it broke". It does not
# catch the things nobody's pager fires for:
#
#   · an encrypted dump that has not been COLLECTED off the server in days,
#     because pull-backups.timer only alerts when the SSH itself fails, not
#     when the server has quietly stopped producing anything new to collect
#   · the one GPG key that decrypts six weeks of backups existing in exactly
#     one keyring, on one laptop, with nothing anywhere confirming it is
#     backed up — which cannot fail loudly, because nothing ever asks it to
#     prove itself until the day it is needed
#   · a restore-test that is still SCHEDULED and SUCCEEDING on a dump that
#     turns out to be weeks stale, because scheduled and succeeding is not
#     the same claim as current
#   · a server disk quietly filling up, which a passing backup does not
#     surface until the night it has nowhere left to write
#
# A system that only tells you when something is already broken is a system
# nobody is actually watching. This is the difference between "did the cron
# fire" and "is this still true" — Charter 03 §IV Tier 1's "monitoring that
# reaches a human" applied to the backups themselves, not just the jobs that
# make them.
#
# ── WHY THIS RUNS ON THE LAPTOP, LIKE pull-backups.sh ───────────────────────
#
# The things worth checking straddle the server (timers, restore-test
# results, disk) and this machine (whether the encrypted dumps actually
# landed, whether the decrypt key is still here). Nothing on the server can
# see both halves, and putting the laptop-only facts into a script that runs
# ON the server would mean copying a GPG fingerprint and a backup directory
# listing to a machine this whole architecture exists to not have to trust.
#
# So it reaches IN over SSH for the server half, the same direction
# pull-backups.sh already established and for the same reason: a pull from
# here costs the server no credential of its own.
#
# ── THE MAIL IS SENT BY THE SERVER, NOT BY THIS SCRIPT ──────────────────────
#
# The Resend key already lives in /opt/gen-portal/backend/.env and is already
# trusted to send operator alerts — see notify.sh. Fetching that key to the
# laptop to send mail FROM HERE would create a second place it can leak for
# no benefit, so the report text is built locally and piped over SSH stdin to
# a one-shot remote python3 that sends it the same way notify.sh does. The
# key never leaves the server it already lives on.
# ═══════════════════════════════════════════════════════════════════════════

set -uo pipefail

HOST="${GENMARS_HOST:-genmars}"
BACKUPS_DIR="${HOME}/genmars-backups"
ALERT_TO="${ALERT_EMAIL:-edwin@genmars.co.ke}"
GPG_FINGERPRINT="${GENMARS_BACKUP_KEY:-AE53386BC6C716F7B7D41AE8413CB8DF5FECF5F4}"

PASS=0
WARN=0
LINES=()

report() {
    local status="$1" text="$2"
    LINES+=("[$status] $text")
    [ "$status" = "WARN" ] && WARN=$((WARN + 1)) || PASS=$((PASS + 1))
}

# ── remote: timers, restore-test results, disk ──────────────────────────────
#
# One SSH round trip for everything server-side, in a stable key=value shape
# so this script parses it rather than scraping `systemctl status` prose that
# changes wording between systemd versions.
#
# `Result` on the paired .service is the authoritative pass/fail signal for a
# oneshot unit — the same property OnFailure= triggers against — not merely
# "did it run".
REMOTE=$(ssh -o BatchMode=yes -o ConnectTimeout=10 "$HOST" '
    for u in genmars-business-backup genmars-business-restore-test \
             genmars-portal-backup genmars-portal-restore-test \
             genmars-business-heartbeat genmars-uptime; do
        echo "ENABLED:${u}:$(systemctl is-enabled ${u}.timer 2>&1)"
        echo "RESULT:${u}:$(systemctl show ${u}.service -p Result --value 2>&1)"
    done
    echo "DISK:$(df -h / --output=pcent | tail -1 | tr -d "% ")"
' 2>&1)

if [ -z "$REMOTE" ] || [[ "$REMOTE" == *"Could not resolve"* ]] || [[ "$REMOTE" == *"Connection refused"* ]]; then
    report WARN "Could not reach $HOST over SSH at all — nothing server-side could be checked this week. $REMOTE"
else
    for unit in genmars-business-backup genmars-business-restore-test \
                genmars-portal-backup genmars-portal-restore-test \
                genmars-business-heartbeat genmars-uptime; do
        enabled=$(grep "^ENABLED:${unit}:" <<<"$REMOTE" | cut -d: -f3)
        result=$(grep "^RESULT:${unit}:" <<<"$REMOTE" | cut -d: -f3)

        if [ "$enabled" != "enabled" ]; then
            report WARN "$unit.timer is '$enabled', not enabled — it will not fire on its own."
        elif [ "$result" = "success" ] || [ -z "$result" ]; then
            report PASS "$unit — enabled, last run succeeded."
        else
            report WARN "$unit — enabled, but its LAST RUN reported '$result', not success. The timer firing on schedule and the job succeeding are different claims; this is the one that matters."
        fi
    done

    # 85, not a rounder number: matching uptime-check.sh's own bar exactly,
    # not inventing a second opinion about the same fact. A looser threshold
    # here would make this script stay quiet on disk pressure the existing
    # check is already actively failing on.
    disk_pct=$(grep "^DISK:" <<<"$REMOTE" | cut -d: -f2)
    if [ -n "$disk_pct" ] && [ "$disk_pct" -ge 85 ] 2>/dev/null; then
        report WARN "Server disk is at ${disk_pct}% — a failed backup write is a worse night than this email. genmars-uptime is already failing on this; see its OnFailure alert."
    elif [ -n "$disk_pct" ]; then
        report PASS "Server disk at ${disk_pct}%."
    fi
fi

# ── local: did the collection actually land, and is the key still here ──────
#
# A timer reporting success proves the SSH worked, not that anything NEW was
# collected. Checking file freshness is what catches a server that has
# quietly stopped producing dumps while the collector keeps succeeding at
# collecting nothing new.
if [ -d "$BACKUPS_DIR" ]; then
    for prefix in business portal; do
        newest=$(ls -t "${BACKUPS_DIR}/${prefix}-"*.dump.gpg 2>/dev/null | head -1)
        if [ -z "$newest" ]; then
            report WARN "No ${prefix}-*.dump.gpg has ever been collected into ${BACKUPS_DIR}."
            continue
        fi
        age_days=$(( ( $(date +%s) - $(stat -c %Y "$newest" 2>/dev/null || stat -f %m "$newest") ) / 86400 ))
        if [ "$age_days" -gt 3 ]; then
            report WARN "Newest ${prefix} backup collected here is ${age_days} days old ($(basename "$newest")) — the daily pull has not brought anything new in a while."
        else
            report PASS "${prefix} backups current — newest is $(basename "$newest"), ${age_days}d old."
        fi
    done
else
    report WARN "${BACKUPS_DIR} does not exist — pull-backups.sh has never run here, or ran somewhere else."
fi

# Sanity only: this proves the key is PRESENT, not that it is backed up
# anywhere else. Nothing on this machine can prove the second thing — that
# is the whole point of needing a copy somewhere that is not this machine.
if gpg --list-secret-keys "$GPG_FINGERPRINT" >/dev/null 2>&1; then
    report PASS "Backup decryption key present in this keyring."
else
    report WARN "Backup decryption key $GPG_FINGERPRINT is NOT in this keyring — every encrypted backup is unreadable from here right now."
fi

# ── the report ────────────────────────────────────────────────────────────
STAMP=$(date -u '+%Y-%m-%d')
BODY=$(printf 'Genmars weekly infrastructure check — %s\n\n%s\n\n%d passed, %d need attention.\n' \
    "$STAMP" "$(printf '%s\n' "${LINES[@]}")" "$PASS" "$WARN")

echo "$BODY"

if [ "$WARN" -eq 0 ]; then
    SUBJECT="[Genmars] Weekly check: all clear ($STAMP)"
else
    SUBJECT="[Genmars] Weekly check: $WARN item(s) need attention ($STAMP)"
fi

# Sent FROM the server, with the key that already lives there — see the
# banner at the top of this file for why. stdin carries the body so it never
# touches a command line or shell history on either end.
printf '%s' "$BODY" | ssh -o BatchMode=yes "$HOST" "
    KEY=\$(grep -m1 '^RESEND_API_KEY=' /opt/gen-portal/backend/.env | cut -d= -f2- | tr -d '\"'\\''')
    BODY=\$(cat)
    if [ -z \"\$KEY\" ]; then
        echo 'weekly-health-check: RESEND_API_KEY empty on server — report not sent' >&2
        exit 0
    fi
    PAYLOAD=\$(python3 -c '
import json, sys
print(json.dumps({
    \"from\": \"info@genmars.co.ke\",
    \"to\": [sys.argv[1]],
    \"subject\": sys.argv[2],
    \"text\": sys.argv[3],
}))' '$ALERT_TO' '$SUBJECT' \"\$BODY\")
    curl -sS -m 20 -X POST https://api.resend.com/emails \
        -H \"Authorization: Bearer \$KEY\" \
        -H 'Content-Type: application/json' \
        -H 'User-Agent: genmars-host-alerts (+https://genmars.co.ke)' \
        -d \"\$PAYLOAD\" >/dev/null
    echo 'weekly-health-check: report sent'
"

exit 0
