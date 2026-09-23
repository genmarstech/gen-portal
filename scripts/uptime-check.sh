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

# ── ONE RETRY BEFORE ANYTHING IS CALLED A FAILURE ───────────────────────────
#
# This paged at 20:45 on 2026-09-23 for clipsserenityspa.co.ke — a LIVE CLIENT
# SITE — and nothing was wrong. Caddy had not restarted in three months, the
# container had been up two weeks, the certificate had sixty-two days left and
# the load average was 0.09. The next run, fifteen minutes later, passed. It
# was one dropped connection on a loopback through the public address.
#
# The timer's own comment already argues this: "a flaky network into a mail
# flood, and an alert channel people learn to ignore is worse than no alert
# channel at all." It defended against that with frequency and not with
# retries, which leaves a single blip able to page.
#
# So: one retry, three seconds later. A real outage fails both attempts and
# still pages within the same run; a blip does not page at all. When the retry
# is what saved it, the report SAYS SO — a check that quietly passes on the
# second attempt hides genuine flakiness, which is the opposite of the
# problem being fixed.
RETRY_PAUSE="${RETRY_PAUSE:-3}"

check() {
    local label="$1" url="$2" want="$3"
    local got note=""

    # NO `|| echo "000"`. curl -w always PRINTS a status — "000" when it could
    # not connect — and then exits non-zero, so the fallback appended a second
    # one and the alert read "got 000000, wanted 200". Six digits where three
    # were meant sends whoever reads it looking for an exotic status code
    # instead of a dropped connection.
    got=$(curl -sS -o /dev/null -m 15 -w "%{http_code}" "$url" 2>/dev/null)
    [ -n "$got" ] || got="000"

    if [ "$got" != "$want" ]; then
        sleep "$RETRY_PAUSE"
        got=$(curl -sS -o /dev/null -m 15 -w "%{http_code}" "$url" 2>/dev/null)
        [ -n "$got" ] || got="000"
        note=" (first attempt failed)"
    fi

    if [ "$got" = "$want" ]; then
        REPORT+=$(printf "  ok    %-34s %s%s\n" "$label" "$got" "$note")
    else
        REPORT+=$(printf "  FAIL  %-34s got %s, wanted %s (twice)\n" "$label" "$got" "$want")
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
#
# clipsserenityspa.co.ke is in this list and was not. Its 200 check above would
# catch an EXPIRED certificate, but only on the day browsers start refusing —
# with no warning window at all. It is a live client site on somebody else's
# domain, which is the one where fourteen days' notice matters most, because
# fixing it may need them to do something.
for host in genmars.co.ke app.genmars.co.ke api.genmars.co.ke ops.genmars.co.ke \
            clipsserenityspa.co.ke; do
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
