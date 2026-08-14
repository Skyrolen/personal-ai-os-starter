"""Tests for verify.py — the policy and verdict logic.

Uses a stub market-data provider so the checks are deterministic and run
offline. That is deliberate: the rules that decide whether real money moves
should be testable without depending on a live feed being up, and the negative
cases (over-cap, earnings blackout, stale entry) need exact inputs to assert on.

Run:  .venv/bin/python tools/test_verify.py
"""

from __future__ import annotations

import datetime as dt
import sys

import numpy as np
import pandas as pd

import verify
from market_data import Fundamentals

FAILURES: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label} {detail}")
        FAILURES.append(label)


# --------------------------------------------------------------------------
# Stub provider
# --------------------------------------------------------------------------

def make_series(bars: int = 300, start: float = 80.0, drift: float = 0.08,
                noise: float = 0.4, seed: int = 11) -> pd.DataFrame:
    """A gently rising series: uptrend, moderate RSI, low ATR%."""
    rng = np.random.default_rng(seed)
    closes = start + np.arange(bars) * drift + rng.normal(0, noise, bars)
    closes = np.maximum(closes, 1.0)
    return pd.DataFrame({
        "Open": closes - 0.1,
        "High": closes + 0.5,
        "Low": closes - 0.5,
        "Close": closes,
        "Volume": rng.integers(4_000_000, 6_000_000, bars),
    }, index=pd.date_range("2025-01-01", periods=bars, freq="B"))


class StubProvider:
    def __init__(self, df: pd.DataFrame, fundamentals: Fundamentals):
        self._df = df
        self._fundamentals = fundamentals

    def history(self, ticker, period="1y", interval="1d"):
        return self._df

    def quote(self, ticker):
        return float(self._df["Close"].iloc[-1])

    def fundamentals(self, ticker):
        return self._fundamentals


def make_fundamentals(**overrides) -> Fundamentals:
    defaults = dict(
        ticker="TEST", name="Test Corp", sector="Technology",
        industry="Software", market_cap=1.2e12, trailing_pe=30.0,
        forward_pe=25.0, revenue_growth=0.18, earnings_growth=0.22,
        debt_to_equity=45.0, avg_volume_10d=5_000_000,
        next_earnings=dt.date.today() + dt.timedelta(days=45),
    )
    defaults.update(overrides)
    return Fundamentals(**defaults)


DF = make_series()
LAST = float(DF["Close"].iloc[-1])


def make_account(**overrides) -> dict:
    account = {
        "account_value": 25_000.0,
        "buying_power": 20_000.0,
        "settled_cash": 20_000.0,
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


def run(call=None, account=None, fundamentals=None, policy=None):
    provider = StubProvider(DF, fundamentals or make_fundamentals())
    return verify.verify(call or make_call(), account or make_account(),
                         policy or verify.Policy(), provider)


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
    brief = run(fundamentals=soon)
    check("earnings raises a block", "earnings" in names(brief.checks, "block"))
    check("verdict is WAIT", brief.verdict == "WAIT", f"got {brief.verdict!r}")

    ok = run(fundamentals=make_fundamentals(
        next_earnings=dt.date.today() + dt.timedelta(days=30)))
    check("30 days out is clean", "earnings" not in names(ok.checks, "block"))


def test_unknown_earnings_is_flagged_not_waved_through():
    print("Unknown earnings date")
    brief = run(fundamentals=make_fundamentals(next_earnings=None))
    check("raises a warning", "earnings" in names(brief.checks, "warn"))
    check("listed as unverified",
          any("earnings" in u for u in brief.unverified))


def test_position_size_cap():
    print("5% position cap")
    account = make_account(positions=[
        {"ticker": "TEST", "shares": 10, "market_value": 1000.0,
         "sector": "Technology"},  # 4% of 25k already held
    ])
    brief = run(account=account)
    check("adding beyond 5% blocks",
          "position_size" in names(brief.checks, "block"),
          f"blocks={[c.name for c in brief.blocks]}")
    check("verdict is DISAGREE", brief.verdict == "DISAGREE",
          f"got {brief.verdict!r}")


def test_position_count_cap():
    print("10 position cap")
    positions = [{"ticker": f"S{i}", "shares": 1, "market_value": 100.0,
                  "sector": "Energy"} for i in range(10)]
    brief = run(account=make_account(positions=positions))
    check("full book blocks a new name",
          "position_count" in names(brief.checks, "block"))
    check("verdict is DISAGREE", brief.verdict == "DISAGREE")


def test_sector_concentration_warns():
    print("Sector concentration")
    positions = [{"ticker": f"T{i}", "shares": 1, "market_value": 2500.0,
                  "sector": "Technology"} for i in range(4)]  # 40% already
    brief = run(account=make_account(positions=positions))
    check("warns on correlated concentration",
          "concentration" in names(brief.checks, "warn"),
          f"warns={names(brief.checks,'warn')}")


def test_liquidity_block():
    print("Liquidity")
    thin = make_fundamentals(avg_volume_10d=50_000)
    brief = run(fundamentals=thin)
    check("thin volume blocks", "liquidity" in names(brief.checks, "block"))
    check("verdict is DISAGREE", brief.verdict == "DISAGREE")


def test_buying_power_block():
    print("Buying power")
    brief = run(account=make_account(buying_power=100.0, settled_cash=100.0))
    check("insufficient buying power blocks",
          "buying_power" in names(brief.checks, "block"))
    check("verdict is DISAGREE", brief.verdict == "DISAGREE")


def test_unsettled_cash_warns():
    print("T+1 settlement")
    brief = run(account=make_account(buying_power=20_000.0, settled_cash=200.0))
    check("warns on unsettled funds",
          "settlement" in names(brief.checks, "warn"),
          f"warns={names(brief.checks,'warn')}")


def test_missing_invalidation():
    print("Missing invalidation level")
    call = make_call()
    del call["invalidation"]
    brief = run(call=call)
    check("blocks by default", "invalidation" in names(brief.checks, "block"))
    check("verdict is WAIT (fixable)", brief.verdict == "WAIT")

    relaxed = run(call=call, policy=verify.Policy(require_invalidation=False))
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
    # Entry ~103.77, invalidation 88.2 (risk ~15.6), target 110 (reward ~6.2)
    poor = run(call=make_call(target=round(LAST * 1.06, 2)))
    check("flags thin risk/reward", "risk_reward" in names(poor.checks, "warn"),
          f"warns={names(poor.checks,'warn')}")

    good = run(call=make_call(target=round(LAST * 1.40, 2)))
    check("accepts healthy risk/reward",
          "risk_reward" in names(good.checks, "pass"))

    passed = run(call=make_call(target=round(LAST * 0.90, 2)))
    check("warns when the target is already passed",
          "target" in names(passed.checks, "warn"))


def test_sizing_math():
    print("Position sizing")
    sizing = verify.compute_sizing(100.0, make_account(account_value=25_000.0),
                                   verify.Policy())
    # 5% of 25,000 = 1,250 budget -> 12 shares at 100 = 1,200
    check("budget is 1250", sizing["budget"] == 1250.0)
    check("12 shares (floor, never over-cap)", sizing["shares"] == 12)
    check("cost 1200", sizing["cost"] == 1200.0)
    check("actual pct 4.8", sizing["actual_pct"] == 4.8)

    unpriced = verify.compute_sizing(100.0, {}, verify.Policy())
    check("no account value -> explains rather than guessing",
          "note" in unpriced and "shares" not in unpriced)


def test_downtrend_warns_on_buy():
    print("Chart disagreement")
    falling = make_series(drift=-0.08, seed=5)
    provider = StubProvider(falling, make_fundamentals())
    last = float(falling["Close"].iloc[-1])
    call = make_call(call_price=round(last, 2),
                     invalidation=round(last * 0.85, 2))
    brief = verify.verify(call, make_account(), verify.Policy(), provider)
    check("downtrend warns against a buy", "trend" in names(brief.checks, "warn"),
          f"warns={names(brief.checks,'warn')}")


def test_verdict_never_upgrades_past_a_block():
    print("Verdict safety")
    thin = make_fundamentals(avg_volume_10d=1000)
    brief = run(fundamentals=thin)
    check("a block never yields AGREE", not brief.verdict.startswith("AGREE"))


def test_render_does_not_crash():
    print("Rendering")
    text = verify.render(run())
    check("renders a brief", "VERDICT" in text and "CHECKS" in text)
    check("carries the not-advice disclaimer", "not advice" in text)
    incomplete = verify.render(run(call={"ticker": "TEST"}))
    check("renders incomplete calls too", "VERDICT" in incomplete)


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
