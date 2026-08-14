"""Trade-idea verification pipeline.

Runs the mechanical checks behind /bora-check and prints a brief. The division
of labour matters:

  - This script does arithmetic and market data: drift, indicators, sizing,
    liquidity, policy limits. Things with a right answer.
  - Claude does judgement and MCP: reading the thesis, calling Robinhood,
    weighing conflicting evidence, deciding.

This script NEVER places an order and has no broker access at all. The worst it
can do is print a wrong number, which is why the checks are separated from the
execution path entirely.

Usage:
    python tools/verify.py --call call.json [--account account.json] [--json]
    python tools/verify.py --ticker NVDA --call-price 120 --call-date 2026-07-01

call.json:
    {
      "ticker": "NVDA",
      "direction": "buy",
      "call_price": 120.00,        // price when the call was made
      "call_date": "2026-07-01",
      "target": 160.00,            // optional
      "invalidation": 104.00,      // optional; thesis-break level
      "thesis": "free text",
      "source": "Skool post 2026-07-01"
    }

account.json (filled by Claude from the Robinhood MCP read tools):
    {
      "account_value": 25000.0,
      "buying_power": 8000.0,
      "settled_cash": 5000.0,
      "positions": [
        {"ticker": "AAPL", "shares": 10, "market_value": 2000.0,
         "sector": "Technology"}
      ]
    }
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
import math
import sys
from dataclasses import dataclass, field

import indicators
from market_data import Fundamentals, MarketDataError, get_provider

# --------------------------------------------------------------------------
# Policy — mirrors 50_Finance/trading-policy.md. Keep the two in sync.
# --------------------------------------------------------------------------

@dataclass
class Policy:
    max_position_pct: float = 5.0
    max_positions: int = 10
    max_sector_pct: float = 30.0
    earnings_blackout_days: int = 3
    max_drift_pct: float = 5.0       # how far past his entry is still "the trade"
    min_avg_volume: float = 500_000  # shares/day; below this, slippage bites
    require_invalidation: bool = True


SEVERITY_ORDER = {"block": 0, "warn": 1, "info": 2, "pass": 3}


@dataclass
class Check:
    name: str
    severity: str          # block | warn | info | pass
    message: str
    detail: str | None = None

    @property
    def icon(self) -> str:
        return {"block": "BLOCK", "warn": "WARN ", "info": "INFO ",
                "pass": "OK   "}[self.severity]


@dataclass
class Brief:
    ticker: str
    verdict: str = "UNKNOWN"
    reason: str = ""
    checks: list[Check] = field(default_factory=list)
    sizing: dict = field(default_factory=dict)
    technicals: dict = field(default_factory=dict)
    unverified: list[str] = field(default_factory=list)

    def add(self, name: str, severity: str, message: str,
            detail: str | None = None) -> None:
        self.checks.append(Check(name, severity, message, detail))

    @property
    def blocks(self) -> list[Check]:
        return [c for c in self.checks if c.severity == "block"]

    @property
    def warnings(self) -> list[Check]:
        return [c for c in self.checks if c.severity == "warn"]


# --------------------------------------------------------------------------
# Individual checks
# --------------------------------------------------------------------------

def check_claim_completeness(call: dict, brief: Brief, policy: Policy) -> None:
    """Missing pieces are reported, never inferred. An unstated stop is not a
    stop, and guessing one would put words in his mouth that you'd then trade."""
    required = ["ticker", "call_price", "call_date"]
    absent = [f for f in required if not call.get(f)]
    if absent:
        brief.add("claim", "block",
                  f"Call is missing required fields: {', '.join(absent)}",
                  "Cannot verify drift or size a position without these.")
    else:
        brief.add("claim", "pass", "Call has ticker, entry price, and date")

    if not call.get("invalidation"):
        severity = "block" if policy.require_invalidation else "warn"
        brief.add("invalidation", severity,
                  "No invalidation level stated in the call",
                  "Policy requires a thesis-break level before entry. If he "
                  "didn't give one, you must set one yourself — do not invent "
                  "one and attribute it to him.")
    else:
        brief.add("invalidation", "pass",
                  f"Invalidation level: {call['invalidation']:.2f}")


def check_levels_coherent(call: dict, last_price: float, brief: Brief) -> None:
    """The invalidation and target must make sense relative to entry.

    A long whose invalidation sits above the entry price is stopped out the
    moment it's opened. That is a transcription error rather than a trade, and
    it is exactly the kind of thing that looks fine in prose and is obvious in
    arithmetic.
    """
    direction = (call.get("direction") or "buy").lower()
    bullish = direction in ("buy", "long", "add", "accumulate")
    invalidation = call.get("invalidation")
    target = call.get("target")

    if invalidation:
        wrong_side = (invalidation >= last_price) if bullish else (invalidation <= last_price)
        if wrong_side:
            side = "above" if bullish else "below"
            brief.add("levels", "block",
                      f"Invalidation {invalidation:.2f} is {side} the current "
                      f"price {last_price:.2f} for a {direction}",
                      "This position would be invalid the instant it opened. "
                      "Re-read the call — either the level or the direction "
                      "was captured wrong.")
        else:
            risk_pct = abs(last_price - invalidation) / last_price * 100.0
            brief.add("levels", "pass",
                      f"Risk to invalidation: {risk_pct:.1f}%")

    if target:
        wrong_side = (target <= last_price) if bullish else (target >= last_price)
        if wrong_side:
            brief.add("target", "warn",
                      f"Target {target:.2f} is already reached or passed "
                      f"(price {last_price:.2f})",
                      "There is no upside left in the trade as he framed it.")
        elif invalidation and not (
            (invalidation >= last_price) if bullish else (invalidation <= last_price)
        ):
            reward = abs(target - last_price)
            risk = abs(last_price - invalidation)
            ratio = reward / risk if risk > 0 else None
            if ratio is None:
                pass
            elif ratio < 1.5:
                brief.add("risk_reward", "warn",
                          f"Risk/reward is only {ratio:.1f}:1 from here",
                          "The move past his entry has eaten the edge: you are "
                          "risking nearly as much as you stand to make.")
            else:
                brief.add("risk_reward", "pass",
                          f"Risk/reward {ratio:.1f}:1 from current price")

    if not call.get("thesis"):
        brief.add("thesis", "warn", "No thesis text captured",
                  "Without the reasoning you cannot tell later whether the "
                  "thesis broke or just the price moved.")


def check_drift(call: dict, last_price: float, brief: Brief,
                policy: Policy) -> None:
    """The highest-value check. If price has run past his entry, the trade
    available today is not the trade he described."""
    call_price = call.get("call_price")
    if not call_price:
        brief.unverified.append("price drift (no call price given)")
        return

    drift = (last_price - call_price) / call_price * 100.0
    age = _age_in_days(call.get("call_date"))
    age_text = f", call is {age} days old" if age is not None else ""

    detail = (f"He called it at {call_price:.2f}; it is {last_price:.2f} now "
              f"({drift:+.1f}%{age_text}).")

    if drift > policy.max_drift_pct:
        brief.add("drift", "block",
                  f"Price has run {drift:+.1f}% past his entry "
                  f"(limit {policy.max_drift_pct:.0f}%)",
                  detail + " Entering here is a materially worse trade than "
                           "the one he described: less upside to target, and "
                           "your invalidation level is now further away.")
    elif drift > policy.max_drift_pct / 2:
        brief.add("drift", "warn", f"Price is {drift:+.1f}% above his entry",
                  detail)
    elif drift < -15.0:
        brief.add("drift", "warn",
                  f"Price is {drift:+.1f}% BELOW his entry", detail +
                  " Cheaper than he paid — but check whether the thesis broke "
                  "rather than assuming it's a discount.")
    else:
        brief.add("drift", "pass", f"Price is {drift:+.1f}% vs his entry", detail)

    if age is not None and age > 90:
        brief.add("staleness", "warn",
                  f"Call is {age} days old",
                  "His published portfolio updates quarterly; this may already "
                  "have been superseded. Confirm he still holds it.")


def check_technicals(read: indicators.TechnicalRead, call: dict,
                     brief: Brief) -> None:
    """Independent read of the chart. Disagreements with his view are the
    entire point of this step and are reported prominently."""
    brief.technicals = {
        "trend": read.trend,
        "last_close": round(read.last_close, 2),
        "sma20": _round(read.sma20), "sma50": _round(read.sma50),
        "sma200": _round(read.sma200),
        "rsi14": _round(read.rsi14, 1),
        "macd": read.macd_state,
        "atr14": _round(read.atr14), "atr_pct": _round(read.atr_pct, 2),
        "volume_vs_avg": _round(read.volume_vs_avg, 2),
        "levels": [str(lv) for lv in read.levels],
    }

    direction = (call.get("direction") or "buy").lower()
    bullish_call = direction in ("buy", "long", "add", "accumulate")

    if read.trend.startswith(("uptrend", "strong")):
        if bullish_call:
            brief.add("trend", "pass", f"Trend is {read.trend} — supports his call")
        else:
            brief.add("trend", "warn",
                      f"Trend is {read.trend} but the call is {direction}")
    elif read.trend == "downtrend":
        if bullish_call:
            brief.add("trend", "warn",
                      "Trend is a downtrend — the chart disagrees with a buy",
                      "Price is below its moving averages. This does not make "
                      "him wrong, but you are buying into weakness.")
        else:
            brief.add("trend", "pass", "Downtrend supports a bearish call")
    else:
        brief.add("trend", "info", f"Trend is {read.trend}")

    if read.rsi14 is not None:
        if read.rsi14 > 70 and bullish_call:
            brief.add("rsi", "warn", f"RSI {read.rsi14:.0f} — overbought",
                      "Buying into an extended move. Waiting for a pullback "
                      "toward support usually costs little at this horizon.")
        elif read.rsi14 < 30 and bullish_call:
            brief.add("rsi", "info", f"RSI {read.rsi14:.0f} — oversold")
        else:
            brief.add("rsi", "pass", f"RSI {read.rsi14:.0f} — neutral range")
    else:
        brief.unverified.append("RSI (insufficient history)")

    if "fresh" in read.macd_state:
        brief.add("macd", "info", f"MACD: {read.macd_state}")

    if read.atr_pct is not None and read.atr_pct > 5.0:
        brief.add("volatility", "warn",
                  f"ATR is {read.atr_pct:.1f}% of price — high volatility",
                  "Size down or widen the invalidation level; a normal day's "
                  "range could otherwise stop you out.")

    for note in read.notes:
        brief.add("data", "info", note)


def check_fundamentals(fund: Fundamentals, brief: Brief, policy: Policy) -> None:
    """A smell test, not a valuation model. Its job is to catch 'the thesis
    already broke' and 'earnings are in two days'."""
    today = dt.date.today()

    if fund.next_earnings:
        days = (fund.next_earnings - today).days
        if 0 <= days <= policy.earnings_blackout_days:
            brief.add("earnings", "block",
                      f"Earnings in {days} day(s) ({fund.next_earnings})",
                      "Policy blackout. A binary event inside the blackout "
                      "window is a coin flip, not the thesis you're buying.")
        elif 0 <= days <= 10:
            brief.add("earnings", "warn",
                      f"Earnings in {days} days ({fund.next_earnings})")
        else:
            brief.add("earnings", "pass",
                      f"Next earnings {fund.next_earnings} ({days} days out)")
    else:
        brief.add("earnings", "warn", "Next earnings date unknown",
                  "Could not confirm the blackout window is clear. Verify "
                  "manually before entering.")
        brief.unverified.append("earnings date")

    if fund.revenue_growth is not None:
        pct = fund.revenue_growth * 100.0
        if pct < 0:
            brief.add("fundamentals", "warn",
                      f"Revenue growth is negative ({pct:.1f}%)",
                      "If his thesis is growth, the growth is not currently there.")
        else:
            brief.add("fundamentals", "pass", f"Revenue growth {pct:+.1f}%")
    else:
        brief.unverified.append("revenue growth")

    if fund.missing:
        brief.add("data", "info",
                  f"Fundamentals unavailable: {', '.join(sorted(set(fund.missing)))}")


def check_tradability(fund: Fundamentals, last_price: float, account: dict,
                      sizing: dict, brief: Brief, policy: Policy) -> None:
    """Is this trade actually executable in this account, today."""
    avg_vol = fund.avg_volume_10d
    if avg_vol is None:
        brief.unverified.append("average volume")
    elif avg_vol < policy.min_avg_volume:
        brief.add("liquidity", "block",
                  f"Average volume {avg_vol:,.0f}/day is below the "
                  f"{policy.min_avg_volume:,.0f} minimum",
                  "Thin book — expect slippage on entry and worse on exit.")
    else:
        brief.add("liquidity", "pass", f"Average volume {avg_vol:,.0f}/day")

    if not account:
        brief.unverified.append("buying power and existing positions "
                                "(no account data supplied)")
        return

    cost = sizing.get("cost", 0.0)
    buying_power = account.get("buying_power")
    settled_cash = account.get("settled_cash")

    if buying_power is None:
        brief.unverified.append("buying power")
    elif cost > buying_power:
        brief.add("buying_power", "block",
                  f"Position costs {cost:,.2f} but buying power is "
                  f"{buying_power:,.2f}")
    else:
        brief.add("buying_power", "pass",
                  f"Cost {cost:,.2f} fits buying power {buying_power:,.2f}")

    # Agentic cash accounts settle T+1, so buying power can include funds that
    # are not actually spendable yet.
    if (settled_cash is not None and buying_power is not None
            and cost > settled_cash and settled_cash < buying_power):
        brief.add("settlement", "warn",
                  f"Cost {cost:,.2f} exceeds settled cash {settled_cash:,.2f}",
                  "On an agentic cash account, unsettled proceeds are not "
                  "tradable for one business day. This order may be rejected "
                  "or trigger a good-faith violation.")


def check_policy(call: dict, account: dict, fund: Fundamentals, sizing: dict,
                 brief: Brief, policy: Policy) -> None:
    """Portfolio-level limits, including the concentration check that matters
    most when copying a book of correlated megacap tech."""
    if not account:
        brief.unverified.append("policy limits (no account data supplied)")
        return

    positions = account.get("positions") or []
    account_value = account.get("account_value") or 0.0
    ticker = (call.get("ticker") or "").upper()

    held = next((p for p in positions
                 if str(p.get("ticker", "")).upper() == ticker), None)

    if len(positions) >= policy.max_positions and not held:
        brief.add("position_count", "block",
                  f"Already holding {len(positions)} positions "
                  f"(max {policy.max_positions})",
                  "Close something before opening a new name.")
    else:
        brief.add("position_count", "pass",
                  f"{len(positions)} of {policy.max_positions} positions used")

    if held and account_value > 0:
        existing_pct = float(held.get("market_value", 0.0)) / account_value * 100.0
        combined = existing_pct + sizing.get("target_pct", 0.0)
        if combined > policy.max_position_pct:
            brief.add("position_size", "block",
                      f"Already {existing_pct:.1f}% in {ticker}; adding would "
                      f"reach {combined:.1f}% (max {policy.max_position_pct:.0f}%)")
        else:
            brief.add("position_size", "pass",
                      f"Existing {existing_pct:.1f}% + new = {combined:.1f}%")

    # Sector concentration. Correlated names are one bet wearing many names.
    sector = fund.sector
    if sector and account_value > 0:
        sector_value = sum(
            float(p.get("market_value", 0.0)) for p in positions
            if str(p.get("sector", "")).lower() == sector.lower()
        )
        new_pct = (sector_value + sizing.get("cost", 0.0)) / account_value * 100.0
        if new_pct > policy.max_sector_pct:
            brief.add("concentration", "warn",
                      f"{sector} would be {new_pct:.0f}% of the account "
                      f"(soft limit {policy.max_sector_pct:.0f}%)",
                      "His published book is concentrated US megacap tech. "
                      "Copying it in full is one macro bet held under eleven "
                      "names — they will draw down together.")
        else:
            brief.add("concentration", "pass",
                      f"{sector} exposure would be {new_pct:.0f}%")
    elif not sector:
        brief.unverified.append("sector concentration (sector unknown)")


def compute_sizing(last_price: float, account: dict, policy: Policy) -> dict:
    """Position size from the policy cap, not from conviction."""
    account_value = (account or {}).get("account_value")
    if not account_value:
        return {"note": "No account value supplied — cannot size the position.",
                "target_pct": policy.max_position_pct}

    budget = account_value * policy.max_position_pct / 100.0
    shares = int(math.floor(budget / last_price)) if last_price > 0 else 0
    cost = shares * last_price
    return {
        "account_value": account_value,
        "target_pct": policy.max_position_pct,
        "budget": round(budget, 2),
        "price": round(last_price, 2),
        "shares": shares,
        "cost": round(cost, 2),
        "actual_pct": round(cost / account_value * 100.0, 2) if account_value else None,
    }


# --------------------------------------------------------------------------
# Verdict
# --------------------------------------------------------------------------

def decide(brief: Brief) -> None:
    """Mechanical verdict from the checks.

    Claude may downgrade this (AGREE -> WAIT/DISAGREE) on qualitative grounds,
    but must never upgrade it: a blocked trade stays blocked.
    """
    blocks = brief.blocks
    if blocks:
        drift_block = any(c.name == "drift" for c in blocks)
        fixable = {"drift", "earnings", "invalidation"}
        if all(c.name in fixable for c in blocks):
            brief.verdict = "WAIT"
            brief.reason = (
                "The idea may be sound but this entry is not takeable as-is: "
                + "; ".join(c.message for c in blocks) + "."
            )
            if drift_block:
                brief.reason += (
                    " Re-check if it pulls back toward his entry, or re-size "
                    "against a fresh invalidation level."
                )
        else:
            brief.verdict = "DISAGREE"
            brief.reason = ("Hard blocks: "
                            + "; ".join(c.message for c in blocks) + ".")
        return

    warnings = brief.warnings
    if len(warnings) >= 3:
        brief.verdict = "WAIT"
        brief.reason = (f"No hard blocks, but {len(warnings)} warnings stack up: "
                        + "; ".join(c.message for c in warnings) + ".")
    elif warnings:
        brief.verdict = "AGREE (with caveats)"
        brief.reason = ("Checks pass. Note: "
                        + "; ".join(c.message for c in warnings) + ".")
    else:
        brief.verdict = "AGREE"
        brief.reason = "All mechanical checks pass."


# --------------------------------------------------------------------------
# Pipeline
# --------------------------------------------------------------------------

def verify(call: dict, account: dict | None = None,
           policy: Policy | None = None, provider=None) -> Brief:
    policy = policy or Policy()
    account = account or {}
    provider = provider or get_provider()

    ticker = (call.get("ticker") or "").strip().upper()
    if not ticker:
        raise ValueError("call must include a 'ticker'")

    brief = Brief(ticker=ticker)
    check_claim_completeness(call, brief, policy)

    df = provider.history(ticker, period="1y", interval="1d")
    read = indicators.analyze(df, ticker)
    last_price = read.last_close

    fund = provider.fundamentals(ticker)
    sizing = compute_sizing(last_price, account, policy)
    brief.sizing = sizing

    check_drift(call, last_price, brief, policy)
    check_levels_coherent(call, last_price, brief)
    check_technicals(read, call, brief)
    check_fundamentals(fund, brief, policy)
    check_tradability(fund, last_price, account, sizing, brief, policy)
    check_policy(call, account, fund, sizing, brief, policy)

    brief.checks.sort(key=lambda c: SEVERITY_ORDER[c.severity])
    decide(brief)
    return brief


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------

def render(brief: Brief) -> str:
    lines: list[str] = []
    add = lines.append

    add("=" * 68)
    add(f"  {brief.ticker} — VERDICT: {brief.verdict}")
    add("=" * 68)
    add("")
    add(_wrap(brief.reason))
    add("")

    add("CHECKS")
    add("-" * 68)
    for check in brief.checks:
        add(f"  [{check.icon}] {check.name:<16} {check.message}")
        if check.detail:
            add(_wrap(check.detail, indent=" " * 12))
    add("")

    tech = brief.technicals
    if tech:
        add("TECHNICALS")
        add("-" * 68)
        add(f"  Last {tech['last_close']}   Trend: {tech['trend']}")
        add(f"  SMA20 {tech['sma20']}   SMA50 {tech['sma50']}   "
            f"SMA200 {tech['sma200']}")
        add(f"  RSI14 {tech['rsi14']}   MACD: {tech['macd']}   "
            f"ATR {tech['atr14']} ({tech['atr_pct']}%)")
        if tech.get("volume_vs_avg"):
            add(f"  Volume vs 20d avg: {tech['volume_vs_avg']}x")
        for level in tech.get("levels", []):
            add(f"  Level: {level}")
        add("")

    sizing = brief.sizing
    if sizing.get("shares") is not None:
        add("SIZING (policy cap, not conviction)")
        add("-" * 68)
        add(f"  {sizing['shares']} shares @ {sizing['price']} = "
            f"{sizing['cost']:,.2f}  ({sizing.get('actual_pct')}% of account)")
        add("")
    elif sizing.get("note"):
        add("SIZING")
        add("-" * 68)
        add(f"  {sizing['note']}")
        add("")

    if brief.unverified:
        add("COULD NOT VERIFY")
        add("-" * 68)
        for item in brief.unverified:
            add(f"  - {item}")
        add("")

    add("-" * 68)
    add("This script places no orders and has no broker access. It is analysis,")
    add("not advice. A verified trade can still lose money.")
    return "\n".join(lines)


def _wrap(text: str, width: int = 68, indent: str = "") -> str:
    import textwrap
    return "\n".join(textwrap.wrap(text, width=width, initial_indent=indent,
                                   subsequent_indent=indent)) or indent


def _round(value: float | None, digits: int = 2) -> float | None:
    return None if value is None else round(value, digits)


def _age_in_days(call_date) -> int | None:
    if not call_date:
        return None
    try:
        parsed = dt.date.fromisoformat(str(call_date)[:10])
    except ValueError:
        return None
    return (dt.date.today() - parsed).days


def _load(path: str | None) -> dict:
    if not path:
        return {}
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify a trade idea against market data and policy.")
    parser.add_argument("--call", help="Path to call JSON")
    parser.add_argument("--account", help="Path to account JSON")
    parser.add_argument("--ticker")
    parser.add_argument("--call-price", type=float)
    parser.add_argument("--call-date")
    parser.add_argument("--target", type=float)
    parser.add_argument("--invalidation", type=float)
    parser.add_argument("--direction", default="buy")
    parser.add_argument("--thesis", default="")
    parser.add_argument("--json", action="store_true",
                        help="Emit JSON instead of the text brief")
    parser.add_argument("--allow-missing-invalidation", action="store_true",
                        help="Downgrade the missing-invalidation block to a "
                             "warning (you must then set one yourself)")
    args = parser.parse_args(argv)

    call = _load(args.call)
    for key, value in (("ticker", args.ticker), ("call_price", args.call_price),
                       ("call_date", args.call_date), ("target", args.target),
                       ("invalidation", args.invalidation),
                       ("thesis", args.thesis), ("direction", args.direction)):
        if value:
            call[key] = value

    if not call.get("ticker"):
        parser.error("a ticker is required (via --call file or --ticker)")

    policy = Policy(require_invalidation=not args.allow_missing_invalidation)

    try:
        brief = verify(call, _load(args.account), policy)
    except MarketDataError as exc:
        print(f"Market data error: {exc}", file=sys.stderr)
        return 2
    except (ValueError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(dataclasses.asdict(brief), indent=2, default=str))
    else:
        print(render(brief))

    return 1 if brief.verdict.startswith("DISAGREE") else 0


if __name__ == "__main__":
    raise SystemExit(main())
