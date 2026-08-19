"""Trade-idea verification pipeline.

Runs the mechanical checks behind /bora-check and prints a brief. The division
of labour matters:

  - This script does arithmetic: drift, indicators, sizing, liquidity, policy
    limits. Things with a right answer.
  - Claude does judgement and MCP: reading the thesis, calling Robinhood,
    weighing conflicting evidence, deciding.

This script NEVER places an order, and has no broker access and no network
access at all. Market data arrives as a JSON file the agent assembled from
Robinhood MCP calls. The worst this can do is print a wrong number.

Usage:
    python tools/verify.py --call call.json --market market.json \
                           [--account account.json] [--json]

See market_data.py for the market.json shape.

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

account.json (from get_portfolio + get_equity_positions on the AGENTIC account):
    {
      "account_value": 3000.0,
      "buying_power": 3000.0,
      "settled_cash": 3000.0,
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
from market_data import (Fundamentals, MarketDataError, MarketSnapshot,
                         Quote, Tradability, load_market_json)

# --------------------------------------------------------------------------
# Policy — mirrors 50_Finance/trading-policy.md. Keep the two in sync.
# --------------------------------------------------------------------------

@dataclass
class Sleeve:
    """A virtual sub-portfolio mirroring one of Bora's sleeves.

    There is only one agentic Robinhood account — sleeves are bookkeeping
    enforced here and recorded in the trade log, not separate broker accounts.
    """
    code: str
    name: str
    weight_pct: float
    max_positions: int = 10
    holdings_drift_pct: float = 20.0
    tradeable: bool = True
    note: str = ""

    def budget(self, risk_base: float) -> float:
        return risk_base * self.weight_pct / 100.0


def default_sleeves() -> dict[str, Sleeve]:
    """Defaults, overridden by risk-profile.json.

    P1/P2/P4/P5 mirror Bora's sub-portfolios at his weights as observed
    2026-08-14, scaled by 0.9 so his RELATIVE proportions are preserved exactly
    while 10% is carved out for P6 — independent, screened ideas that must
    never be mixed into a sleeve that mirrors his book.
    """
    return {
        "P1": Sleeve("P1", "Yatirim", 66.465, 14, 20.0),
        "P4": Sleeve("P4", "Trade", 13.122, 3, 5.0),
        "P2": Sleeve("P2", "Moonshot", 5.589, 6, 15.0),
        "P5": Sleeve("P5", "Opsiyon", 4.824, 0, 5.0, tradeable=False,
                     note="agentic account has no option level"),
        "P6": Sleeve("P6", "Kendi", 10.0, 4, 5.0,
                     note="my own screened ideas, never his"),
    }


@dataclass
class Policy:
    # Position size is a percentage of RISK_BASE — declared investable capital —
    # not of the agentic account balance. Sizing off the balance would let a
    # transfer inflate the cap: move money in to fund a trade you like and "5%"
    # quietly becomes 100%. The cap only constrains if its base is set
    # independently of the trade in front of you.
    risk_base: float = 20_000.0
    max_position_pct: float = 5.0     # -> $1,000 absolute ceiling
    sleeve_position_pct: float = 25.0  # no name may exceed 25% of its sleeve
    max_positions: int = 10
    max_sector_pct: float = 30.0
    earnings_blackout_days: int = 3
    # A dated transaction has a real entry price, so a stale one is a different
    # trade. A holdings average cost is a blended reference point across many
    # buys — judging it by the same 5% would block every winner he owns.
    transaction_drift_pct: float = 5.0
    min_avg_volume: float = 500_000   # shares/day; below this, slippage bites
    max_spread_pct: float = 0.5       # bid/ask as % of mid
    require_invalidation: bool = True
    sleeves: dict = field(default_factory=default_sleeves)

    @property
    def max_position_value(self) -> float:
        return self.risk_base * self.max_position_pct / 100.0

    def sleeve(self, code: str) -> Sleeve:
        try:
            return self.sleeves[code.upper()]
        except KeyError:
            raise ValueError(
                f"Unknown sleeve {code!r}. Known: {sorted(self.sleeves)}"
            ) from None

    def drift_limit(self, sleeve: Sleeve, source: str) -> float:
        """Tiered: strict against a dated transaction, looser against an
        average cost."""
        if source in ("transaction", "screen"):
            # A screen identifies a SPECIFIC entry level, like a transaction —
            # not a blended average. If price has run away from the level the
            # setup was built on, the setup is gone.
            return self.transaction_drift_pct
        return sleeve.holdings_drift_pct

    @property
    def total_max_positions(self) -> int:
        """Across all sleeves. Derived so it can't silently contradict the
        per-sleeve limits — P1 alone allows 14, which the old flat 10 forbade."""
        total = sum(s.max_positions for s in self.sleeves.values() if s.tradeable)
        return total or self.max_positions

    def position_ceiling(self, sleeve: Sleeve) -> float:
        """Lower of the absolute cap and a share of the sleeve.

        The second term stops one name dominating a small sleeve: at P2's
        weight the sleeve is ~$1,242, so 25% caps a moonshot near $310 rather
        than letting it take half the sleeve.
        """
        return min(self.max_position_value,
                   sleeve.budget(self.risk_base) * self.sleeve_position_pct / 100.0)


SEVERITY_ORDER = {"block": 0, "warn": 1, "info": 2, "pass": 3}

# Blocks that describe a fixable circumstance rather than a bad idea. These
# yield WAIT (revisit later) instead of DISAGREE (don't take this trade).
FIXABLE_BLOCKS = {"drift", "earnings", "invalidation", "funding"}

# `over_cap` and `sleeve_untradeable` are deliberately NOT fixable: one needs an
# explicit override on the record, the other is structurally impossible.


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
    sleeve: str = ""
    source_type: str = ""
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

    if not call.get("thesis"):
        brief.add("thesis", "warn", "No thesis text captured",
                  "Without the reasoning you cannot tell later whether the "
                  "thesis broke or just the price moved.")


def check_drift(call: dict, last_price: float, brief: Brief, policy: Policy,
                limit_pct: float, source: str) -> None:
    """The highest-value check. If price has run past his entry, the trade
    available today is not the trade he described.

    `limit_pct` is tiered by (sleeve, source): a dated transaction gets the
    strict limit; a holdings average cost gets the sleeve's looser one, because
    a blended cost across many buys is a reference point, not an entry signal.
    """
    call_price = call.get("call_price")
    if not call_price:
        brief.unverified.append("price drift (no call price given)")
        return

    drift = (last_price - call_price) / call_price * 100.0
    age = _age_in_days(call.get("call_date"))
    age_text = f", call is {age} days old" if age is not None else ""

    basis = ("his entry" if source == "transaction" else "his average cost")
    detail = (f"{'He bought at' if source == 'transaction' else 'His average cost is'} "
              f"{call_price:.2f}; it is {last_price:.2f} now "
              f"({drift:+.1f}%{age_text}).")

    if drift > limit_pct:
        brief.add("drift", "block",
                  f"Price is {drift:+.1f}% above {basis} "
                  f"(limit {limit_pct:.0f}%)",
                  detail + " Entering here is a materially worse trade than "
                           "the one he took: less upside to target, and "
                           "your invalidation level is now further away.")
    elif drift > limit_pct / 2:
        brief.add("drift", "warn", f"Price is {drift:+.1f}% above {basis}",
                  detail)
    elif drift < -15.0:
        brief.add("drift", "warn",
                  f"Price is {drift:+.1f}% BELOW {basis}", detail +
                  " Cheaper than he paid — but check whether the thesis broke "
                  "rather than assuming it's a discount.")
    else:
        brief.add("drift", "pass", f"Price is {drift:+.1f}% vs {basis}", detail)

    if age is not None and age > 90:
        brief.add("staleness", "warn",
                  f"Call is {age} days old",
                  "His published portfolio updates quarterly; this may already "
                  "have been superseded. Confirm he still holds it.")


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


def check_tradability(tradability: Tradability, quote: Quote,
                      fund: Fundamentals, brief: Brief, policy: Policy) -> None:
    """Can this actually be traded, on this account, right now.

    Robinhood's tradability flags are authoritative — they know about halts and
    account-level restrictions that volume and price cannot reveal.
    """
    if tradability.tradable is False:
        reason = f" ({tradability.reason})" if tradability.reason else ""
        brief.add("tradable", "block",
                  f"Robinhood reports {fund.ticker} as not tradable on this "
                  f"account{reason}")
    elif tradability.tradable is True:
        sessions = ", ".join(tradability.sessions) if tradability.sessions else "regular"
        brief.add("tradable", "pass", f"Tradable ({sessions})")
    else:
        brief.unverified.append("tradability (not supplied)")

    spread = quote.spread_pct
    if spread is not None:
        if spread > policy.max_spread_pct:
            brief.add("spread", "warn",
                      f"Bid/ask spread is {spread:.2f}% of mid",
                      "Wide book — you pay this twice, on the way in and the "
                      "way out.")
        else:
            brief.add("spread", "pass", f"Spread {spread:.2f}% of mid")
    else:
        brief.unverified.append("bid/ask spread")

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


def check_funding(account: dict, sizing: dict, brief: Brief) -> None:
    """Is the cash in the right account to take this at full size?

    A funding gap is not a reason to shrink the position — that quietly caps
    winners while leaving losers full-size. It is a reason to say exactly how
    much to move and wait. The agent never initiates a transfer.
    """
    if not account:
        brief.unverified.append("buying power (no account data supplied)")
        return

    cost = sizing.get("cost")
    buying_power = account.get("buying_power")
    settled_cash = account.get("settled_cash")

    if cost is None or buying_power is None:
        brief.unverified.append("buying power")
        return

    gap = sizing.get("funding_gap") or 0.0
    if gap > 0:
        brief.add("funding", "block",
                  f"Need {cost:,.2f} but the agentic account has "
                  f"{buying_power:,.2f} — transfer {gap:,.2f}",
                  "The idea is fine; the cash is in the wrong account. Move "
                  f"{gap:,.2f} into the agentic account and re-run. Do not "
                  "size down to fit — that caps your winners and leaves your "
                  "losers at full size.")
    else:
        brief.add("funding", "pass",
                  f"Cost {cost:,.2f} fits buying power {buying_power:,.2f}")

    # Agentic cash accounts settle T+1, so buying power can include funds that
    # are not actually spendable yet.
    if (settled_cash is not None and cost > settled_cash
            and settled_cash < buying_power):
        brief.add("settlement", "warn",
                  f"Cost {cost:,.2f} exceeds settled cash {settled_cash:,.2f}",
                  "This is a cash account: unsettled proceeds are not tradable "
                  "for one business day. This order may be rejected or trigger "
                  "a good-faith violation.")


def check_policy(call: dict, account: dict, fund: Fundamentals, sizing: dict,
                 brief: Brief, policy: Policy) -> None:
    """Portfolio-level limits, including the concentration check that matters
    most when copying a book of correlated megacap tech."""
    if not account:
        brief.unverified.append("policy limits (no account data supplied)")
        return

    positions = account.get("positions") or []
    ticker = (call.get("ticker") or "").upper()

    held = next((p for p in positions
                 if str(p.get("ticker", "")).upper() == ticker), None)

    total_max = policy.total_max_positions
    if len(positions) >= total_max and not held:
        brief.add("position_count", "block",
                  f"Already holding {len(positions)} positions "
                  f"(max {total_max} across all sleeves)",
                  "Close something before opening a new name.")
    else:
        brief.add("position_count", "pass",
                  f"{len(positions)} of {total_max} positions used overall")

    # Existing exposure counts against the same ceiling, measured against the
    # risk base rather than the account balance so it stays comparable.
    if held:
        existing = float(held.get("market_value", 0.0))
        combined = existing + sizing.get("cost", 0.0)
        ceiling = sizing.get("ceiling", policy.max_position_value)
        if combined > ceiling:
            brief.add("position_size", "block",
                      f"Already {existing:,.2f} in {ticker}; adding would reach "
                      f"{combined:,.2f} (ceiling {ceiling:,.2f})")
        else:
            brief.add("position_size", "pass",
                      f"Existing {existing:,.2f} + new = {combined:,.2f} "
                      f"(ceiling {ceiling:,.2f})")

    # Sector concentration. Correlated names are one bet wearing many names.
    sector = fund.sector
    if sector:
        sector_value = sum(
            float(p.get("market_value", 0.0)) for p in positions
            if str(p.get("sector", "")).lower() == sector.lower()
        )
        new_value = sector_value + sizing.get("cost", 0.0)
        new_pct = new_value / policy.risk_base * 100.0
        if new_pct > policy.max_sector_pct:
            brief.add("concentration", "warn",
                      f"{sector} would be {new_pct:.0f}% of the risk base "
                      f"(soft limit {policy.max_sector_pct:.0f}%)",
                      "His published book is concentrated US megacap tech. "
                      "Copying it in full is one macro bet held under eleven "
                      "names — they will draw down together.")
        else:
            brief.add("concentration", "pass",
                      f"{sector} exposure would be {new_pct:.0f}% of risk base")
    else:
        brief.unverified.append("sector concentration (sector unknown)")


def compute_sizing(last_price: float, account: dict, policy: Policy,
                   sleeve: Sleeve, budget: float | None = None) -> dict:
    """Recommend a size, and price whatever budget the user named.

    The agent never sizes silently. It proposes `recommended` — an equal weight
    within the sleeve, capped by `ceiling` — shows the ceiling, and asks. If the
    user names a budget it is used as-is; anything above the ceiling is caught
    by check_position_budget rather than being quietly clipped, so an override
    is a decision on the record instead of an invisible adjustment.
    """
    sleeve_budget = sleeve.budget(policy.risk_base)
    ceiling = policy.position_ceiling(sleeve)

    # Equal weight within the sleeve is the neutral prior: a cap is a ceiling,
    # not a target, so the default proposal is not simply "the maximum".
    even = (sleeve_budget / sleeve.max_positions) if sleeve.max_positions else 0.0
    recommended = round(min(even, ceiling), 2) if even else round(ceiling, 2)

    chosen = float(budget) if budget else recommended
    shares = int(math.floor(chosen / last_price)) if last_price > 0 else 0
    cost = round(shares * last_price, 2)

    sizing = {
        "risk_base": policy.risk_base,
        "sleeve": sleeve.code,
        "sleeve_name": sleeve.name,
        "sleeve_budget": round(sleeve_budget, 2),
        "ceiling": round(ceiling, 2),
        "recommended": recommended,
        "chosen": round(chosen, 2),
        "user_set": bool(budget),
        "price": round(last_price, 2),
        "shares": shares,
        "cost": cost,
        "pct_of_risk_base": round(cost / policy.risk_base * 100.0, 2)
        if policy.risk_base else None,
        "pct_of_sleeve": round(cost / sleeve_budget * 100.0, 2)
        if sleeve_budget else None,
    }

    if shares == 0:
        sizing["note"] = (
            f"One share costs {last_price:,.2f}, above the {chosen:,.2f} "
            f"budget for this position. Either skip this name or take it as a "
            f"fractional order (market orders, regular hours only)."
        )

    buying_power = (account or {}).get("buying_power")
    if buying_power is not None:
        sizing["buying_power"] = buying_power
        sizing["funding_gap"] = round(max(0.0, cost - float(buying_power)), 2)

    return sizing


def check_his_direction(call: dict, brief: Brief,
                        recency_days: int = 45) -> None:
    """Was his own last action in this name a SELL?

    The MU case exposed this: his most recent action was a trim, and that fact
    sat in the transaction log instead of leading the brief. Buying what the
    person you follow is actively reducing may still be right — his trims are
    often rebalancing — but it must never be something you find out later.
    Warn, don't block; and never bury it.
    """
    last = call.get("his_last_action")
    if not last:
        brief.unverified.append(
            "his last action in this name (not filled from transactions.md)")
        return

    action = str(last.get("action", "")).lower()
    action = {"alım": "buy", "alim": "buy", "satış": "sell",
              "satis": "sell"}.get(action, action)
    when = last.get("date")
    age = _age_in_days(when)
    price = last.get("price")
    note = last.get("note") or ""

    if action == "sell" and age is not None and age <= recency_days:
        price_text = f" at {float(price):.2f}" if price else ""
        detail = ("Buying what he is reducing can still be right — his trims "
                  "are often rebalancing, not conviction changes — but it is "
                  "the single most important fact on the page and must not be "
                  "discovered after entry.")
        if note:
            detail += f' His stated reason: "{note}"'
        brief.add("his_direction", "warn",
                  f"He last SOLD this name on {when}{price_text}", detail)
    elif action == "sell":
        brief.add("his_direction", "info",
                  f"His last action was a sell, but {age} days ago — stale")
    elif action == "buy":
        brief.add("his_direction", "pass",
                  f"His last action was a buy on {when}")


def check_manageability(sizing: dict, snapshot_fractional: bool | None,
                        brief: Brief, min_shares: int = 3) -> None:
    """Can this position be managed after entry, or only opened and closed?

    The MU case: $972.78/share against a $1,000 ceiling buys exactly 1 share.
    No trim into strength, no scale-in on weakness, no cutting half when a
    warning escalates — the only move is all-or-nothing. That is a structurally
    bad way to hold a name, and worst for exactly the volatile names where it
    happens.
    """
    shares = sizing.get("shares")
    ceiling = sizing.get("ceiling", 0.0)
    price = sizing.get("price", 0.0)
    if shares is None or not price:
        return

    max_shares = int(ceiling // price) if price > 0 else 0
    if max_shares >= min_shares:
        return

    if snapshot_fractional:
        alternative = ("Fractional orders are available on this name, which "
                       "restores the ability to scale and trim — at the cost "
                       "of market-order-only execution in regular hours (no "
                       "limit-price protection on entry).")
    elif snapshot_fractional is False:
        alternative = ("Fractional orders are NOT available on this name, so "
                       "there is no way around the all-or-nothing structure "
                       "at this budget.")
    else:
        alternative = ("Check whether fractional orders are available; they "
                       "would restore scaling at the cost of market-order-only "
                       "execution.")

    brief.add("manageability", "warn",
              f"The {ceiling:,.2f} ceiling buys only {max_shares} whole "
              f"share(s) at {price:,.2f}",
              "A position this granular cannot be trimmed into strength, "
              "scaled on weakness, or half-cut when a warning escalates — the "
              "only available action is all-or-nothing. " + alternative)


def check_position_budget(sizing: dict, policy: Policy, sleeve: Sleeve,
                          brief: Brief) -> None:
    """The size gate. Under the ceiling passes without comment; over it is not
    refused, but it must be an explicit, logged override."""
    chosen = sizing.get("chosen", 0.0)
    ceiling = sizing.get("ceiling", 0.0)

    if chosen > ceiling + 0.005:
        over = chosen - ceiling
        brief.add("over_cap", "block",
                  f"Budget {chosen:,.2f} exceeds the {ceiling:,.2f} ceiling "
                  f"for {sleeve.code} by {over:,.2f}",
                  f"The ceiling is the lower of {policy.max_position_pct:.0f}% "
                  f"of the risk base ({policy.max_position_value:,.2f}) and "
                  f"{policy.sleeve_position_pct:.0f}% of the {sleeve.code} "
                  f"sleeve ({sleeve.budget(policy.risk_base) * policy.sleeve_position_pct / 100.0:,.2f}). "
                  "This is not a refusal: say plainly that you are overriding "
                  "the cap and it will be logged as an override, so the "
                  "scorecard can tell you later whether overriding paid.")
    elif sizing.get("user_set"):
        brief.add("position_budget", "pass",
                  f"Budget {chosen:,.2f} is within the {ceiling:,.2f} ceiling")
    else:
        brief.add("position_budget", "info",
                  f"Proposed {chosen:,.2f} (ceiling {ceiling:,.2f}) — "
                  "awaiting your approval or a different amount")


def check_sleeve(sleeve: Sleeve, account: dict, sizing: dict, policy: Policy,
                 brief: Brief) -> None:
    """Sleeve-level limits: tradability, position count, and budget headroom."""
    if not sleeve.tradeable:
        reason = f" ({sleeve.note})" if sleeve.note else ""
        brief.add("sleeve_untradeable", "block",
                  f"{sleeve.code} {sleeve.name} cannot be traded in this "
                  f"account{reason}",
                  "Its share of the risk base is held as cash. This is a "
                  "structural limit, not a judgement about the idea.")
        return

    if not account:
        brief.unverified.append(f"{sleeve.code} sleeve usage (no account data)")
        return

    positions = [p for p in (account.get("positions") or [])
                 if str(p.get("sleeve", "")).upper() == sleeve.code]

    if len(positions) >= sleeve.max_positions:
        brief.add("sleeve_count", "block",
                  f"{sleeve.code} already holds {len(positions)} positions "
                  f"(max {sleeve.max_positions})")
    else:
        brief.add("sleeve_count", "pass",
                  f"{sleeve.code}: {len(positions)} of {sleeve.max_positions} "
                  f"positions used")

    used = sum(float(p.get("market_value", 0.0)) for p in positions)
    sleeve_budget = sizing.get("sleeve_budget", 0.0)
    remaining = sleeve_budget - used
    cost = sizing.get("cost", 0.0)

    if cost > remaining:
        brief.add("sleeve_budget", "block",
                  f"{sleeve.code} has {remaining:,.2f} of {sleeve_budget:,.2f} "
                  f"left; this position needs {cost:,.2f}",
                  "Trim an existing position in this sleeve, or take a smaller "
                  "size. Do not borrow budget from another sleeve — that is "
                  "how the structure stops meaning anything.")
    else:
        brief.add("sleeve_budget", "pass",
                  f"{sleeve.code} headroom {remaining:,.2f} of "
                  f"{sleeve_budget:,.2f}")


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
        block_names = {c.name for c in blocks}
        if block_names <= FIXABLE_BLOCKS:
            brief.verdict = "WAIT"
            brief.reason = (
                "The idea may be sound but this entry is not takeable as-is: "
                + "; ".join(c.message for c in blocks) + "."
            )
            if "drift" in block_names:
                brief.reason += (
                    " Re-check if it pulls back toward his entry, or re-size "
                    "against a fresh invalidation level."
                )
            if "funding" in block_names:
                brief.reason += (
                    " Move the cash, then re-run — do not size down to fit."
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

def verify(call: dict, snapshot: MarketSnapshot, account: dict | None = None,
           policy: Policy | None = None, budget: float | None = None) -> Brief:
    policy = policy or Policy()
    account = account or {}

    ticker = (call.get("ticker") or "").strip().upper()
    if not ticker:
        raise ValueError("call must include a 'ticker'")
    if ticker != snapshot.ticker:
        raise ValueError(
            f"call is for {ticker} but market data is for {snapshot.ticker}"
        )

    sleeve = policy.sleeve(call.get("sleeve") or "P1")
    source = (call.get("source_type") or "holdings").lower()
    if source not in ("transaction", "holdings", "screen"):
        raise ValueError(
            f"source_type must be 'transaction', 'holdings' or 'screen', "
            f"got {source!r}"
        )

    brief = Brief(ticker=ticker)
    brief.sleeve = sleeve.code
    brief.source_type = source
    check_claim_completeness(call, brief, policy)

    read = indicators.analyze(snapshot.bars, ticker)
    last_price = snapshot.last_price
    fund = snapshot.fundamentals
    sizing = compute_sizing(last_price, account, policy, sleeve, budget)
    brief.sizing = sizing

    # An independent screen idea has no "his direction" — he has no opinion
    # on it. Asking would report unverified every time, which is noise.
    if source != "screen":
        check_his_direction(call, brief)
    check_drift(call, last_price, brief, policy,
                policy.drift_limit(sleeve, source), source)
    check_levels_coherent(call, last_price, brief)
    check_technicals(read, call, brief)
    check_fundamentals(fund, brief, policy)
    check_tradability(snapshot.tradability, snapshot.quote, fund, brief, policy)
    check_manageability(sizing, snapshot.tradability.fractional, brief)
    check_position_budget(sizing, policy, sleeve, brief)
    check_sleeve(sleeve, account, sizing, policy, brief)
    check_funding(account, sizing, brief)
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
    header = f"  {brief.ticker} — VERDICT: {brief.verdict}"
    if brief.sleeve:
        header += f"   [{brief.sleeve} / {brief.source_type}]"
    add(header)
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
    add("SIZING — proposal, not a decision")
    add("-" * 68)
    add(f"  Sleeve {sizing.get('sleeve')} {sizing.get('sleeve_name', '')}   "
        f"budget {sizing.get('sleeve_budget', 0):,.2f}   "
        f"ceiling {sizing.get('ceiling', 0):,.2f}")
    if sizing.get("user_set"):
        add(f"  You set: {sizing.get('chosen', 0):,.2f} "
            f"(recommended was {sizing.get('recommended', 0):,.2f})")
    else:
        add(f"  Recommended: {sizing.get('recommended', 0):,.2f}")
    if sizing.get("shares"):
        add(f"  {sizing['shares']} shares @ {sizing['price']:,.2f} = "
            f"{sizing['cost']:,.2f}   "
            f"({sizing.get('pct_of_sleeve')}% of sleeve, "
            f"{sizing.get('pct_of_risk_base')}% of risk base)")
    if sizing.get("note"):
        add(_wrap(sizing["note"], indent="  "))
    if sizing.get("funding_gap"):
        add(f"  TRANSFER NEEDED: {sizing['funding_gap']:,.2f} "
            f"(have {sizing.get('buying_power', 0):,.2f})")
    add("")
    add("  >> Approve at this amount, or name a different one. Nothing is")
    add("     placed until you approve the exact ticket.")
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


RISK_PROFILE_PATH = "50_Finance/private/risk-profile.json"


def _load_risk_profile() -> dict:
    """Read the gitignored private profile: risk base and sleeve weights.

    Lives outside the committed policy because it reveals portfolio size. If it
    is absent the Policy defaults apply — and the brief still shows which base
    and sleeve it sized against, so a wrong number is visible rather than silent.
    """
    import os
    for candidate in (RISK_PROFILE_PATH,
                      os.path.join("..", RISK_PROFILE_PATH)):
        try:
            with open(candidate, encoding="utf-8") as handle:
                return json.load(handle)
        except (FileNotFoundError, json.JSONDecodeError):
            continue
    return {}


def _sleeves_from_profile(profile: dict) -> dict | None:
    raw = profile.get("sleeves")
    if not raw:
        return None
    sleeves = {}
    for code, spec in raw.items():
        sleeves[code.upper()] = Sleeve(
            code=code.upper(),
            name=spec.get("name", code),
            weight_pct=float(spec.get("weight_pct", 0.0)),
            max_positions=int(spec.get("max_positions", 10)),
            holdings_drift_pct=float(spec.get("holdings_drift_pct", 20.0)),
            tradeable=bool(spec.get("tradeable", True)),
            note=spec.get("note", ""),
        )
    return sleeves


def _load(path: str | None) -> dict:
    if not path:
        return {}
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify a trade idea against market data and policy.")
    parser.add_argument("--call", help="Path to call JSON")
    parser.add_argument("--market", required=True,
                        help="Path to market JSON (assembled from Robinhood MCP)")
    parser.add_argument("--account", help="Path to account JSON")
    parser.add_argument("--ticker")
    parser.add_argument("--call-price", type=float)
    parser.add_argument("--call-date")
    parser.add_argument("--target", type=float)
    parser.add_argument("--invalidation", type=float)
    parser.add_argument("--direction", default="buy")
    parser.add_argument("--thesis", default="")
    parser.add_argument("--risk-base", type=float,
                        help="Override declared investable capital")
    parser.add_argument("--sleeve", default=None,
                        help="Which virtual sleeve: P1, P2, P4, P5")
    parser.add_argument("--source-type", default=None,
                        choices=["transaction", "holdings"],
                        help="'transaction' = a dated buy from his history "
                             "(strict drift); 'holdings' = his average cost "
                             "(sleeve's looser drift limit)")
    parser.add_argument("--budget", type=float,
                        help="Position budget you are approving. Omit to see "
                             "the recommendation and the ceiling first.")
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
                       ("thesis", args.thesis), ("direction", args.direction),
                       ("sleeve", args.sleeve),
                       ("source_type", args.source_type)):
        if value:
            call[key] = value

    profile = _load_risk_profile()
    policy = Policy(require_invalidation=not args.allow_missing_invalidation)
    risk_base = args.risk_base or profile.get("risk_base")
    if risk_base:
        policy.risk_base = float(risk_base)
    sleeves = _sleeves_from_profile(profile)
    if sleeves:
        policy.sleeves = sleeves

    try:
        snapshot = load_market_json(args.market)
        if not call.get("ticker"):
            call["ticker"] = snapshot.ticker
        brief = verify(call, snapshot, _load(args.account), policy, args.budget)
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
