#!/usr/bin/env bash
# Fires the Daily News Brief workflow via GitHub's workflow_dispatch API.
# Production uses cron-job.org (see docs/runbook.md); this is the equivalent for a
# Linux server cron entry at 07:15 IST (server clock in IST):
#   15 7 * * * /opt/daily-news-brief/dispatch.sh >> /var/log/dnb-dispatch.log 2>&1
# Requires: curl; the PAT in $TOKEN_FILE (chmod 600, one line).
set -u
TOKEN_FILE="${TOKEN_FILE:-$HOME/.daily_news_brief_gh_token}"
REPO="harshad22491/Daily-News-Brief"
ALERT_TO="harshad422@gmail.com"

TOKEN=$(cat "$TOKEN_FILE") || { echo "$(date -u +%FT%TZ) token file missing: $TOKEN_FILE"; exit 1; }

STATUS=$(curl -sS -o /tmp/dnb_dispatch_body.txt -w '%{http_code}' -X POST \
  -H "Authorization: Bearer $TOKEN" \
  -H "Accept: application/vnd.github+json" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  "https://api.github.com/repos/$REPO/actions/workflows/daily.yml/dispatches" \
  -d '{"ref":"main"}')

if [ "$STATUS" = "204" ]; then
  echo "$(date -u +%FT%TZ) dispatched OK"
  exit 0
fi

MSG="$(date -u +%FT%TZ) dispatch FAILED (HTTP $STATUS): $(cat /tmp/dnb_dispatch_body.txt)"
echo "$MSG"
# Optional local alert if the server has a mail command configured.
command -v mail >/dev/null && echo "$MSG" | mail -s "Daily News Brief dispatch FAILED" "$ALERT_TO"
exit 1
