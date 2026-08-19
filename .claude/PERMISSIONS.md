# Why the allowlist looks like this

`.claude/settings.json` pre-approves only the **read-only** path, so an
unattended `/bora-daily` can run without a human to click "allow":

| Entry | Why |
|---|---|
| `Bash(curl -s -m 5 http://127.0.0.1:9222/*)` | Chrome health check. Localhost and that port only — it cannot reach the internet. |
| `Bash(scripts/notify.sh *)` | Push. The script itself refuses placeholder topics and any message containing a dollar amount. |
| `Bash(.venv/bin/python tools/*)` | The offline analysis engine. No network, no broker access. |
| `mcp__chrome-devtools__*` | Reading his platform and Skool through your logged-in profile. |
| `mcp__robinhood-trading__get_*` | Account and market **reads**. |
| `..._search`, `..._run_scan`, `..._create_scan` | The screener. Read-only. |

## What is deliberately NOT here

`place_equity_order`, `place_option_order`, `cancel_*`, `exercise_option`.

They are **omitted from the allowlist, not denied.** That distinction matters:

- **Omitted** → every call prompts you for approval. `/bora-green` still works,
  and the prompt becomes a third gate on top of the size approval and the
  ticket approval.
- **Denied** → the tool is blocked outright and cannot be approved even
  interactively, which would break `/bora-green` completely.

So the headless daily run can never place an order — it has no approver — while
your interactive sessions can, once you say yes. That is the property we want,
and it comes from leaving them out rather than from banning them.

## If you widen this

Anything you add here runs unattended, with your logged-in browser sessions
attached. Add read-only tools freely; think hard before adding anything that
writes, spends, or sends.
