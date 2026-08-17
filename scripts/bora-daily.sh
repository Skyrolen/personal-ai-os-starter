#!/bin/bash
# Headless daily run, invoked by launchd.
#
# launchd gives you a minimal environment: no ~/.zshrc, a bare PATH, no shell
# functions. Everything this needs is set explicitly below.
#
# Exit codes: 0 ok, 1 chrome unavailable, 2 claude failed.

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT" || exit 1

export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
PROFILE="$HOME/chrome-claude-profile"
PORT=9222
LOG_DIR="$REPO_ROOT/50_Finance/private/logs"
LOG="$LOG_DIR/bora-daily-$(date +%Y-%m-%d).log"

mkdir -p "$LOG_DIR"
exec >>"$LOG" 2>&1
echo "=== $(date '+%Y-%m-%d %H:%M:%S') starting ==="

chrome_up() {
    curl -s -m 5 "http://127.0.0.1:$PORT/json/version" | grep -q '"Browser"'
}

if ! chrome_up; then
    echo "port $PORT down — launching the debug profile"
    nohup "$CHROME" \
        --remote-debugging-port="$PORT" \
        --user-data-dir="$PROFILE" >/dev/null 2>&1 &
    disown

    for _ in $(seq 1 20); do
        sleep 1
        chrome_up && break
    done
fi

if ! chrome_up; then
    echo "FAILED: chrome debug port never came up"
    "$REPO_ROOT/scripts/notify.sh" "Bora daily FAILED: Chrome debug port down"
    exit 1
fi

echo "chrome ok — running /bora-daily"

# The command itself verifies the SESSION is authenticated. Chrome answering on
# 9222 only proves the browser is running, not that the Skool and platform
# logins are still valid — and a scan against a logged-out session would
# produce a confident, empty, wrong report.
if ! claude -p "/bora-daily" --permission-mode acceptEdits; then
    echo "FAILED: claude exited non-zero"
    "$REPO_ROOT/scripts/notify.sh" "Bora daily FAILED: run errored, check the log"
    exit 2
fi

echo "=== $(date '+%Y-%m-%d %H:%M:%S') done ==="
