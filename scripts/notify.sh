#!/bin/bash
# Push a terse alert via ntfy.sh.
#
# Terse BY DESIGN: an ntfy topic is readable by anyone who guesses its name,
# so this carries actions and tickers only — never balances, position sizes,
# account numbers, or P&L. If you would not post it publicly, it does not go
# through here.
#
# Config (gitignored): 50_Finance/private/notify.json
#   {"ntfy_topic": "some-hard-to-guess-string", "include_tickers": true}
#
# Usage:  scripts/notify.sh "Bora: 2 buys, 1 sell. 3 takeable."
#         scripts/notify.sh --dry-run "test message"

set -uo pipefail

DRY_RUN=0
if [ "${1:-}" = "--dry-run" ]; then
    DRY_RUN=1
    shift
fi

MESSAGE="${1:-}"
if [ -z "$MESSAGE" ]; then
    echo "usage: notify.sh [--dry-run] <message>" >&2
    exit 2
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG="$REPO_ROOT/50_Finance/private/notify.json"

if [ ! -f "$CONFIG" ]; then
    echo "notify: no config at $CONFIG — skipping push" >&2
    echo "  create it with: {\"ntfy_topic\": \"your-topic\"}" >&2
    exit 0
fi

TOPIC=$(sed -n 's/.*"ntfy_topic"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$CONFIG")
if [ -z "$TOPIC" ]; then
    echo "notify: ntfy_topic missing from $CONFIG — skipping push" >&2
    exit 0
fi

# Guard: refuse a placeholder or guessable topic.
#
# This exists because a copy-pasteable setup snippet once shipped with
# "pick-something-long-and-random-here" as the example value, and an example
# that looks like a real value gets pasted as one. An ntfy topic IS the
# credential: anyone who guesses the string reads every push.
case "$TOPIC" in
    pick-something-long-and-random-here|your-topic|your-long-random-topic-here|\
    changeme|test-topic*|bora|bora-daily)
        echo "notify: topic '$TOPIC' is a placeholder — refusing to send." >&2
        echo "  Generate a real one:  openssl rand -hex 16" >&2
        echo "  Then put it in $CONFIG and subscribe to it in the ntfy app." >&2
        exit 1
        ;;
esac

if [ "${#TOPIC}" -lt 20 ]; then
    echo "notify: topic is only ${#TOPIC} chars — too short to be unguessable." >&2
    echo "  An ntfy topic is the only thing protecting these pushes." >&2
    echo "  Generate one with:  openssl rand -hex 16" >&2
    exit 1
fi

# Guard against the obvious leak: refuse anything that looks like money.
if printf '%s' "$MESSAGE" | grep -qE '\$[0-9]'; then
    echo "notify: message contains a dollar amount — refusing to send." >&2
    echo "  The ntfy topic is public to anyone who guesses it. Send counts and" >&2
    echo "  tickers, not balances or position sizes." >&2
    exit 1
fi

if [ "$DRY_RUN" -eq 1 ]; then
    echo "DRY RUN — would POST to https://ntfy.sh/$TOPIC"
    echo "  title: Bora"
    echo "  body:  $MESSAGE"
    exit 0
fi

HTTP=$(curl -s -o /dev/null -w '%{http_code}' -m 15 \
    -H "Title: Bora" \
    -H "Tags: chart_with_upwards_trend" \
    -d "$MESSAGE" \
    "https://ntfy.sh/$TOPIC")

if [ "$HTTP" != "200" ]; then
    echo "notify: ntfy returned HTTP $HTTP" >&2
    exit 1
fi
echo "notify: sent"
