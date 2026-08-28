#!/usr/bin/env bash
# Print the most recent verification / reset code from the local mail directory.
#
# Development only: EMAIL_BACKEND is the file backend, so "sending" an email
# writes it to backend/sent-emails/ instead of delivering it anywhere.
set -euo pipefail
cd "$(dirname "$0")/../backend"
latest=$(ls -t sent-emails/*.log 2>/dev/null | head -1) || { echo "No emails sent yet."; exit 1; }
grep -E "^To:|^Subject:|Your code is" "$latest"
