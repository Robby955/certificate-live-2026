#!/bin/sh
# Daily prospective run, intended for 12:30 UTC: predict tomorrow from today's 00Z run, anchor it, resolve
# the day seven days back, certify the resolved prefix, upgrade pending anchors, commit and push. Idempotent per day.
set -u
cd /Users/robsneiderman/Projects/certificate-live-2026 || exit 1
export PATH=/Users/robsneiderman/Library/Python/3.13/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/Users/robsneiderman/.elan/bin
LOG=live/job-log.txt
TOMORROW=$(date -u -v+1d +%Y-%m-%d)
BACK=$(date -u -v-7d +%Y-%m-%d)
{
  echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) start"
  python3 jobs/live.py predict --valid-date "$TOMORROW" && python3 jobs/live.py anchor --valid-date "$TOMORROW"
  python3 jobs/live.py resolve --valid-date "$BACK"
  python3 jobs/live.py certify
  python3 jobs/live.py upgrade
  git add live && git -c user.email=robbysneiderman@gmail.com -c user.name="Robby Sneiderman" commit -q -m "Daily run $(date -u +%Y-%m-%d): predictions for $TOMORROW, outcomes for $BACK, certificates and anchor status" || echo "nothing to commit"
  git push -q origin main || echo "push failed"
  echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) end"
} >> "$LOG" 2>&1
