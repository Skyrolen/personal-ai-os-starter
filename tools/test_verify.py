"""Tests for verify.py — the policy and verdict logic.

Builds MarketSnapshots directly so the checks are deterministic and run
offline. That is deliberate: the rules that decide whether real money moves
should be testable without a live feed, and the negative cases (over-cap,
earnings blackout, stale entry, funding gap) need exact inputs to assert on.

Run:  .venv/bin/python tools/test_verify.py
"""

from __future__ import annotations

import datetime as dt
import sys

import numpy as np
import pandas as pd

import verify
from market_data import (Fundamentals, MarketSnapshot, Quote, Tradability,
                         parse_market)

FAILURES: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label} {detail}")
        FAILURES.append(label)


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------

def make_bars(count: int = 300, start: float = 80.0, drift: float = 0.08,
              noise: float = 0.4, seed: int = 11) -> pd.DataFrame:
    """A gently rising series: uptrend, moderate RSI, low ATR%."""
    rng = np.random.default_rng(seed)
    closes = start + np.arange(count) * drift + rng.normal(0, noise, count)
    closes = np.maximum(closes, 1.0)
    return pd.DataFrame({
        "open": closes - 0.1,
        "high": closes + 0.5,
        "low": closes - 0.5,
        "close": closes,
        "volume": rng.integers(4_000_000, 6_000_000, count),
    }, index=pd.date_range("2025-01-01", periods=count, freq="B"))


BARS = make_bars()
LAST = float(BARS["close"].iloc[-1])


def make_fundamentals(**overrides) -> Fundamentals:
    defaults = dict(
        ticker="TEST", name="Test Corp", sector="Technology",
        industry="Software", market_cap=1.2e12, trailing_pe=30.0,
        forward_pe=25.0, revenue_growth=0.18, earnings_growth=0.22,
        debt_to_equity=45.0, avg_volume_10d=5_000_000,
        next_earnings=dt.date.today() + dt.timedelta(days=45),
        missing=[],
    )
    defaults.update(overrides)
    return Fundamentals(**defaults)


def make_snapshot(bars=None, fundamentals=None, tradability=None,
                  quote=None) -> MarketSnapshot:
    frame = BARS if bars is None else bars
    last = float(frame["close"].iloc[-1])
    return MarketSnapshot(
        ticker="TEST",
        bars=frame,
        fundamentals=fundamentals or make_fundamentals(),
        tradability=tradability or Tradability(tradable=True, fractional=True,
                                               sessions=["regular"]),
        quote=quote or Quote(last=last, bid=last - 0.02, ask=last + 0.02),
    )


def make_account(**overrides) -> dict:
    account = {
        "account_value": 3_000.0,
        "buying_power": 3_000.0,
        "settled_cash": 3_000.0,
        "positions": [],
    }
    account.update(overrides)
    return account


def make_call(**overrides) -> dict:
    call = {
        "ticker": "TEST",
        "direction": "buy",
        "call_price": round(LAST, 2),        # no drift by default
        "call_date": dt.date.today().isoformat(),
        "invalidation": round(LAST * 0.85, 2),
        "thesis": "Test thesis",
    }
    call.update(overrides)
    return call


def run(call=None, account=None, snapshot=None, policy=None):
    return verify.verify(call or make_call(), snapshot or make_snapshot(),
                         account or make_account(), policy or verify.Policy())


def names(checks, severity):
    return {c.name for c in checks if c.severity == severity}


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------

def test_clean_trade_agrees():
    print("Clean trade")
    brief = run()
    check("verdict is AGREE", brief.verdict.startswith("AGREE"),
          f"got {brief.verdict!r}; warnings={names(brief.checks,'warn')}")
    check("no hard blocks", not brief.blocks,
          f"blocks={[c.message for c in brief.blocks]}")


def test_sizing_uses_risk_base_not_account_balance():
    """The core fix: a transfer into the agentic account must not inflate the
    position cap."""
    print("Sizing anchors to risk base")
    policy = verify.Policy(risk_base=20_000.0, max_position_pct=5.0)
    check("max position is 5% of 20k", policy.max_position_value == 1_000.0)

    small = verify.compute_sizing(100.0, make_account(buying_power=3_000.0), policy)
    big = verify.compute_sizing(100.0, make_account(buying_power=50_000.0), policy)
    check("budget ignores the account balance",
          small["budget"] == big["budget"] == 1_000.0)
    check("10 shares at 100 (floor)", small["shares"] == 10)
    check("cost 1000", small["cost"] == 1000.0)

    # The failure this prevents: sizing off a freshly-funded balance.
    inflated = verify.compute_sizing(100.0, make_account(buying_power=20_000.0),
                                     policy)
    check("funding the account does not raise the cap",
          inflated["shares"] == 10 and inflated["cost"] == 1000.0)


def test_funding_gap_reports_transfer_amount():
    print("Funding gap")
    policy = verify.Policy(risk_base=20_000.0)
    # 1000 position vs 400 buying power -> transfer 600
    brief = run(account=make_account(buying_power=400.0, settled_cash=400.0),
                policy=policy)
    check("raises a funding block", "funding" in names(brief.checks, "block"),
          f"blocks={[c.name for c in brief.blocks]}")
    check("verdict is WAIT, not DISAGREE", brief.verdict == "WAIT",
          f"got {brief.verdict!r}")
    check("tells me not to size down", "size down" in brief.reason.lower())

    gap = brief.sizing["funding_gap"]
    expected = round(brief.sizing["cost"] - 400.0, 2)
    check("transfer amount is exact", gap == expected, f"got {gap} vs {expected}")

    funded = run(account=make_account(buying_power=5_000.0, settled_cash=5_000.0))
    check("no gap when funded", not funded.sizing.get("funding_gap"))
    check("funding passes when funded", "funding" in names(funded.checks, "pass"))


def test_share_price_above_cap():
    print("Share price above the cap")
    expensive = make_bars(count=300, start=2_000.0, drift=0.5, noise=2.0)
    brief = run(call=make_call(call_price=round(float(expensive["close"].iloc[-1]), 2),
                               invalidation=round(float(expensive["close"].iloc[-1]) * 0.85, 2)),
                snapshot=make_snapshot(bars=expensive))
    check("0 shares", brief.sizing["shares"] == 0)
    check("explains the fractional option",
          "fractional" in brief.sizing.get("note", "").lower())


def test_drift_blocks_stale_entry():
    """The headline check: he called it lower, price ran, trade is different."""
    print("Drift past his entry")
    brief = run(call=make_call(call_price=round(LAST / 1.20, 2)))  # ~+20%
    check("drift raises a block", "drift" in names(brief.checks, "block"))
    check("verdict is WAIT", brief.verdict == "WAIT", f"got {brief.verdict!r}")
    check("reason mentions re-checking on a pullback",
          "pull" in brief.reason.lower())

    small = run(call=make_call(call_price=round(LAST / 1.03, 2)))  # ~+3%
    check("3% drift does not block", "drift" not in names(small.checks, "block"))


def test_earnings_blackout():
    print("Earnings blackout")
    soon = make_fundamentals(next_earnings=dt.date.today() + dt.timedelta(days=2))
    brief = run(snapshot=make_snapshot(fundamentals=soon))
    check("earnings raises a block", "earnings" in names(brief.checks, "block"))
    check("verdict is WAIT", brief.verdict == "WAIT", f"got {brief.verdict!r}")

    ok = run(snapshot=make_snapshot(fundamentals=make_fundamentals(
        next_earnings=dt.date.today() + dt.timedelta(days=30))))
    check("30 days out is clean", "earnings" not in names(ok.checks, "block"))


def test_unknown_earnings_is_flagged_not_waved_through():
    print("Unknown earnings date")
    brief = run(snapshot=make_snapshot(
        fundamentals=make_fundamentals(next_earnings=None)))
    check("raises a warning", "earnings" in names(brief.checks, "warn"))
    check("listed as unverified",
          any("earnings" in u for u in brief.unverified))


def test_tradability_flags():
    print("Robinhood tradability")
    halted = Tradability(tradable=False, reason="halted")
    brief = run(snapshot=make_snapshot(tradability=halted))
    check("not-tradable blocks", "tradable" in names(brief.checks, "block"))
    check("verdict is DISAGREE", brief.verdict == "DISAGREE")

    unknown = run(snapshot=make_snapshot(tradability=Tradability()))
    check("missing tradability is unverified, not assumed OK",
          any("tradability" in u for u in unknown.unverified))


def test_spread_warning():
    print("Bid/ask spread")
    wide = Quote(last=LAST, bid=LAST * 0.99, ask=LAST * 1.01)
    brief = run(snapshot=make_snapshot(quote=wide))
    check("wide spread warns", "spread" in names(brief.checks, "warn"),
          f"warns={names(brief.checks,'warn')}")

    tight = run()
    check("tight spread passes", "spread" in names(tight.checks, "pass"))


def test_position_size_cap():
    print("Position cap against risk base")
    account = make_account(positions=[
        {"ticker": "TEST", "shares": 10, "market_value": 800.0,
         "sector": "Technology"},
    ], buying_power=5_000.0)
    brief = run(account=account)
    check("existing 800 + new 1000 exceeds the 1000 cap",
          "position_size" in names(brief.checks, "block"),
          f"blocks={[c.name for c in brief.blocks]}")
    check("verdict is DISAGREE", brief.verdict == "DISAGREE",
          f"got {brief.verdict!r}")


def test_position_count_cap():
    print("10 position cap")
    positions = [{"ticker": f"S{i}", "shares": 1, "market_value": 100.0,
                  "sector": "Energy"} for i in range(10)]
    brief = run(account=make_account(positions=positions, buying_power=5_000.0))
    check("full book blocks a new name",
          "position_count" in names(brief.checks, "block"))
    check("verdict is DISAGREE", brief.verdict == "DISAGREE")


def test_sector_concentration_warns():
    print("Sector concentration")
    positions = [{"ticker": f"T{i}", "shares": 1, "market_value": 1_500.0,
                  "sector": "Technology"} for i in range(4)]  # 6000 of 20000
    brief = run(account=make_account(positions=positions, buying_power=5_000.0))
    check("warns on correlated concentration",
          "concentration" in names(brief.checks, "warn"),
          f"warns={names(brief.checks,'warn')}")


def test_liquidity_block():
    print("Liquidity")
    thin = make_fundamentals(avg_volume_10d=50_000)
    brief = run(snapshot=make_snapshot(fundamentals=thin))
    check("thin volume blocks", "liquidity" in names(brief.checks, "block"))
    check("verdict is DISAGREE", brief.verdict == "DISAGREE")


def test_unsettled_cash_warns():
    print("T+1 settlement")
    brief = run(account=make_account(buying_power=5_000.0, settled_cash=200.0))
    check("warns on unsettled funds",
          "settlement" in names(brief.checks, "warn"),
          f"warns={names(brief.checks,'warn')}")


def test_missing_invalidation():
    print("Missing invalidation level")
    call = make_call()
    del call["invalidation"]
    brief = run(call=call, account=make_account(buying_power=5_000.0))
    check("blocks by default", "invalidation" in names(brief.checks, "block"))
    check("verdict is WAIT (fixable)", brief.verdict == "WAIT")

    relaxed = run(call=call, account=make_account(buying_power=5_000.0),
                  policy=verify.Policy(require_invalidation=False))
    check("downgrades to warn when policy relaxed",
          "invalidation" in names(relaxed.checks, "warn"))


def test_missing_required_fields():
    print("Incomplete call")
    brief = run(call={"ticker": "TEST", "invalidation": 10.0})
    check("blocks on missing entry price/date",
          "claim" in names(brief.checks, "block"))
    check("verdict is DISAGREE", brief.verdict == "DISAGREE")
    check("drift listed as unverified",
          any("drift" in u for u in brief.unverified))


def test_invalidation_must_be_below_entry_for_a_long():
    """A long whose stop sits above the entry is stopped out on open — a
    transcription error that reads fine in prose and is obvious in arithmetic."""
    print("Level coherence")
    brief = run(call=make_call(invalidation=round(LAST * 1.05, 2)))
    check("blocks an invalidation above price",
          "levels" in names(brief.checks, "block"),
          f"blocks={[c.name for c in brief.blocks]}")
    check("verdict is DISAGREE, not WAIT", brief.verdict == "DISAGREE",
          f"got {brief.verdict!r}")

    ok = run()
    check("normal invalidation passes with risk %",
          "levels" in names(ok.checks, "pass"))

    short = run(call=make_call(direction="sell",
                               invalidation=round(LAST * 1.10, 2)))
    check("short with invalidation above price is coherent",
          "levels" not in names(short.checks, "block"))


def test_risk_reward():
    print("Risk / reward")
    poor = run(call=make_call(target=round(LAST * 1.06, 2)))
    check("flags thin risk/reward", "risk_reward" in names(poor.checks, "warn"),
          f"warns={names(poor.checks,'warn')}")

    good = run(call=make_call(target=round(LAST * 1.40, 2)))
    check("accepts healthy risk/reward",
          "risk_reward" in names(good.checks, "pass"))

    passed = run(call=make_call(target=round(LAST * 0.90, 2)))
    check("warns when the target is already passed",
          "target" in names(passed.checks, "warn"))


def test_downtrend_warns_on_buy():
    print("Chart disagreement")
    falling = make_bars(drift=-0.08, seed=5)
    last = float(falling["close"].iloc[-1])
    call = make_call(call_price=round(last, 2),
                     invalidation=round(last * 0.85, 2))
    brief = run(call=call, snapshot=make_snapshot(bars=falling))
    check("downtrend warns against a buy", "trend" in names(brief.checks, "warn"),
          f"warns={names(brief.checks,'warn')}")


def test_ticker_mismatch_is_rejected():
    """Verifying a call against another symbol's data would be catastrophic and
    silent — assert it raises instead."""
    print("Ticker mismatch")
    try:
        verify.verify(make_call(ticker="OTHER"), make_snapshot(),
                      make_account(), verify.Policy())
        check("raises on mismatched ticker", False, "no exception")
    except ValueError as exc:
        check("raises on mismatched ticker", "OTHER" in str(exc))


def test_verdict_never_upgrades_past_a_block():
    print("Verdict safety")
    thin = make_fundamentals(avg_volume_10d=1000)
    brief = run(snapshot=make_snapshot(fundamentals=thin))
    check("a block never yields AGREE", not brief.verdict.startswith("AGREE"))


def test_market_json_parsing():
    print("market.json parsing")
    payload = {
        "ticker": "nvda",
        "quote": {"last_trade_price": "180.25", "bid_price": "180.20",
                  "ask_price": "180.30"},
        "bars": [
            {"begins_at": "2026-08-11", "open_price": "178", "high_price": "181",
             "low_price": "177", "close_price": "180", "volume": "41000000"},
            {"begins_at": "2026-08-12", "open_price": "180", "high_price": "182",
             "low_price": "179", "close_price": "181", "volume": "39000000"},
            {"begins_at": "2026-08-13", "open_price": "181", "high_price": "183",
             "low_price": "180", "close_price": "182", "volume": "40000000",
             "interpolated": True},
        ],
        "fundamentals": {"sector": "Technology", "average_volume": "40000000",
                         "next_earnings": "2026-09-27"},
        "tradability": [{"tradable": True, "fractional": False}],
    }
    snap = parse_market(payload)
    check("uppercases the ticker", snap.ticker == "NVDA")
    check("renames open_price -> open", "open" in snap.bars.columns)
    check("drops interpolated bars", len(snap.bars) == 2)
    check("coerces numerics", float(snap.bars["close"].iloc[-1]) == 181.0)
    check("prefers live quote over last bar", snap.last_price == 180.25)
    check("parses tradability from a list", snap.tradability.tradable is True)
    check("parses the earnings date",
          snap.fundamentals.next_earnings == dt.date(2026, 9, 27))
    spread = snap.quote.spread_pct
    check("computes spread", spread is not None and spread < 0.1)


def test_render_does_not_crash():
    print("Rendering")
    text = verify.render(run())
    check("renders a brief", "VERDICT" in text and "CHECKS" in text)
    check("shows the risk base", "Risk base" in text)
    check("carries the not-advice disclaimer", "not advice" in text)

    gapped = verify.render(run(account=make_account(buying_power=100.0)))
    check("surfaces the transfer amount", "TRANSFER NEEDED" in gapped)


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S): {', '.join(FAILURES)}")
        return 1
    print(f"All {len(tests)} verify test groups passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
