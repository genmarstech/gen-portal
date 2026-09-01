#!/usr/bin/env bash
#
# Does every Genmars surface actually answer, from outside the containers?
#
# ── WHY THIS EXISTS ──────────────────────────────────────────────────────────
# Everything else on this box watches a PART of the system. Docker healthchecks
# ask a container whether it thinks it is fine. Caddy's active health checks ask
# the same of an upstream. Django emails ADMINS on an uncaught 500.
#
# None of them notice the failures that actually take a site down:
#
#   · Caddy fails to start after a reload, so every site is gone while every
#     container reports healthy
#   · a certificate fails to renew and browsers refuse the connection
#   · a DNS record is changed and traffic stops arriving
#   · the disk fills and the database goes read-only
#
# Each of those looks perfect from the inside. This looks from the outside, over
# real TLS, at the URLs a person would type.
#
# ── WHY A SHELL SCRIPT AND NOT A MONITORING SERVICE ─────────────────────────
# Charter 03 §I: something enters the stack only when what is already here
# genuinely cannot do the job. For six URLs on one box, curl and a systemd timer
# can do the job. Revisit when there are enough hosts that a dashboard beats an
# email — not before, because an alerting channel nobody reads is worse than
# none.
#
# Exits non-zero on failure, which is what makes the systemd unit send mail
# (OnFailure=). Prints a report either way so `systemctl status` is readable.

set -uo pipefail

FAILURES=0
REPORT=""

check() {
    local label="$1" url="$2" want="$3"
    local got
    got=$(curl -sS -o /dev/null -m 15 -w "%{http_code}" "$url" 2>/dev/null || echo "000")
    if [ "$got" = "$want" ]; then
        REPORT+=$(printf "  ok    %-34s %s\n" "$label" "$got")
    else
        REPORT+=$(printf "  FAIL  %-34s got %s, wanted %s\n" "$label" "$got" "$want")
        FAILURES=$((FAILURES + 1))
    fi
    REPORT+=$'\n'
}

# The public surfaces, as a person would reach them.
check "marketing site"      "https://genmars.co.ke/"                 200
check "client portal"       "https://app.genmars.co.ke/sign-in"      200
check "portal api"          "https://api.genmars.co.ke/api/health"   200
check "operations"          "https://ops.genmars.co.ke/"             200
check "client site (clips)" "https://clipsserenityspa.co.ke/"        200

# The API through the portal's own proxy hop — this is the path every signed-in
# request takes, and it can break while both ends look healthy. It did exactly
# that once, when Next's rewrite pointed the web container at itself.
check "portal -> django proxy" "https://app.genmars.co.ke/api/auth/session" 200
check "ops -> django proxy"    "https://ops.genmars.co.ke/api/auth/session" 200

# Staff-only endpoints must refuse an anonymous caller. A 200 here would mean
# the operations API had been left open to the internet.
check "ops api refuses anon"   "https://ops.genmars.co.ke/api/ops/overview" 403

# ── certificate expiry ───────────────────────────────────────────────────────
# Caddy renews automatically, so this is a check that automation is WORKING
# rather than a reminder to do it by hand. Fourteen days is enough warning to
# fix a broken renewal before anyone sees a browser warning.
for host in genmars.co.ke app.genmars.co.ke api.genmars.co.ke ops.genmars.co.ke; do
    end=$(echo | openssl s_client -connect "$host:443" -servername "$host" 2>/dev/null \
        | openssl x509 -noout -enddate 2>/dev/null | cut -d= -f2)
    if [ -z "$end" ]; then
        REPORT+=$(printf "  FAIL  %-34s no certificate\n" "cert $host")$'\n'
        FAILURES=$((FAILURES + 1))
        continue
    fi
    days=$(( ( $(date -d "$end" +%s) - $(date +%s) ) / 86400 ))
    if [ "$days" -lt 14 ]; then
        REPORT+=$(printf "  FAIL  %-34s expires in %s days\n" "cert $host" "$days")$'\n'
        FAILURES=$((FAILURES + 1))
    else
        REPORT+=$(printf "  ok    %-34s %s days left\n" "cert $host" "$days")$'\n'
    fi
done

# ── disk ─────────────────────────────────────────────────────────────────────
# A full disk takes Postgres read-only and every container with it, and it is
# the most predictable outage there is: image layers accumulate on every deploy.
USED=$(df --output=pcent / | tail -1 | tr -dc '0-9')
if [ "$USED" -ge 85 ]; then
    REPORT+=$(printf "  FAIL  %-34s %s%% used\n" "disk" "$USED")$'\n'
    FAILURES=$((FAILURES + 1))
else
    REPORT+=$(printf "  ok    %-34s %s%% used\n" "disk" "$USED")$'\n'
fi

printf "Genmars uptime check — %s\n\n%s\n" "$(date -u '+%Y-%m-%d %H:%M UTC')" "$REPORT"

if [ "$FAILURES" -gt 0 ]; then
    echo "$FAILURES check(s) failed."
    exit 1
fi
echo "All checks passed."
