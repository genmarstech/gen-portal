#!/usr/bin/env bash
#
# Send an alert email. Called by systemd when a unit fails.
#
#   scripts/notify.sh <unit-name>
#
# ── WHY RESEND AND NOT AN MTA ────────────────────────────────────────────────
# This box has no mail transport, and installing one to send a handful of alerts
# means a listening daemon, a queue, and another thing to patch. Django already
# sends through Resend over HTTPS (accounts/mail_backends.py); this uses the
# same key and the same path. Nothing new enters the stack — Charter 03 §I.
#
# ── WHY IT EXISTS AT ALL ─────────────────────────────────────────────────────
# genmars-portal-backup.service has carried this comment since it was written:
#
#     "OnFailure needs a matching genmars-portal-backup-failed@.service to
#      actually notify. Until that exists, the honest position is that failures
#      are visible in the journal, and nowhere else."
#
# A backup that fails into a journal nobody reads is not a backup, and Charter
# 03 §IV Tier 1 asks for monitoring that "reaches a human". This is that.
#
# It must NEVER exit non-zero in a way that masks the original failure: the unit
# that called it has already failed, and an error here would replace a useful
# report with a confusing one. Failures are logged and swallowed.

set -uo pipefail

UNIT="${1:-unknown unit}"
ENV_FILE="/opt/gen-portal/backend/.env"
TO="info@genmars.co.ke"
FROM="info@genmars.co.ke"

if [ ! -r "$ENV_FILE" ]; then
    echo "notify: cannot read $ENV_FILE — no alert sent for $UNIT" >&2
    exit 0
fi

# Read the key WITHOUT sourcing the file. `source` would execute it, and it
# holds a Django SECRET_KEY that may contain characters a shell would treat as
# syntax.
KEY=$(grep -m1 '^RESEND_API_KEY=' "$ENV_FILE" | cut -d= -f2- | tr -d '"'"'"'')
if [ -z "$KEY" ]; then
    echo "notify: RESEND_API_KEY empty — no alert sent for $UNIT" >&2
    exit 0
fi

# The last few journal lines are what makes the alert actionable. Without them
# the mail says "something failed" and the reader still has to SSH in.
CONTEXT=$(journalctl -u "$UNIT" -n 25 --no-pager 2>/dev/null | tail -25)
[ -z "$CONTEXT" ] && CONTEXT="(no journal output available)"

BODY=$(printf '%s failed on %s at %s UTC.\n\nLast journal lines:\n\n%s\n\n--\nSent by scripts/notify.sh on the Genmars host.\n' \
    "$UNIT" "$(hostname)" "$(date -u '+%Y-%m-%d %H:%M')" "$CONTEXT")

# python3 for the JSON, not hand-rolled escaping: the journal contains quotes,
# backslashes and newlines, and a broken payload means no alert at the moment
# an alert matters most.
PAYLOAD=$(python3 -c '
import json, sys
print(json.dumps({
    "from": sys.argv[1],
    "to": [sys.argv[2]],
    "subject": "[Genmars] %s failed" % sys.argv[3],
    "text": sys.argv[4],
}))' "$FROM" "$TO" "$UNIT" "$BODY" 2>/dev/null)

if [ -z "$PAYLOAD" ]; then
    echo "notify: could not build payload for $UNIT" >&2
    exit 0
fi

# The same User-Agent lesson as the Django backend: Cloudflare fronts Resend and
# blocks default library agents with a 403 that mentions neither mail nor the
# key. curl sends its own, which is fine — but do not remove it.
RESPONSE=$(curl -sS -m 20 -X POST https://api.resend.com/emails \
    -H "Authorization: Bearer ${KEY}" \
    -H "Content-Type: application/json" \
    -H "User-Agent: genmars-host-alerts (+https://genmars.co.ke)" \
    -d "$PAYLOAD" 2>&1)

echo "notify: $UNIT -> ${RESPONSE:0:200}"
exit 0
