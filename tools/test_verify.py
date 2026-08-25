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
        "sleeve": "P1",
        "source_type": "transaction",
    }
    call.update(overrides)
    return call


def run(call=None, account=None, snapshot=None, policy=None, budget=None):
    return verify.verify(call or make_call(), snapshot or make_snapshot(),
                         account or make_account(), policy or verify.Policy(),
                         budget)


def names(checks, severity):
    return {c.name for c in checks if c.severity == severity}


def _raises(fn) -> bool:
    try:
        fn()
    except ValueError:
        return True
    except Exception:
        return False
    return False


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
    p1 = policy.sleeve("P1")
    check("max position is 5% of 20k", policy.max_position_value == 1_000.0)

    small = verify.compute_sizing(100.0, make_account(buying_power=3_000.0),
                                  policy, p1)
    big = verify.compute_sizing(100.0, make_account(buying_power=50_000.0),
                                policy, p1)
    check("ceiling ignores the account balance",
          small["ceiling"] == big["ceiling"] == 1_000.0)

    # The failure this prevents: sizing off a freshly-funded balance.
    inflated = verify.compute_sizing(100.0, make_account(buying_power=20_000.0),
                                     policy, p1)
    check("funding the account does not raise the ceiling",
          inflated["ceiling"] == 1_000.0)


def test_sleeve_budgets_match_his_weights():
    print("Sleeve budgets")
    policy = verify.Policy(risk_base=20_000.0)
    expected = {"P1": 13_293.0, "P4": 2_624.0, "P2": 1_118.0, "P5": 965.0,
                "P6": 2_000.0}
    for code, want in expected.items():
        got = round(policy.sleeve(code).budget(20_000.0), 0)
        check(f"{code} budget {want:,.0f}", got == want, f"got {got}")

    total = sum(s.weight_pct for s in policy.sleeves.values())
    check("weights total 100%", round(total, 3) == 100.0, f"got {total}")

    # The carve-out must preserve his RELATIVE proportions exactly, or the
    # mirror silently stops being a mirror.
    ratio = policy.sleeve("P1").weight_pct / policy.sleeve("P4").weight_pct
    check("P1:P4 ratio still matches his 73.85:14.58",
          abs(ratio - (73.85 / 14.58)) < 1e-9, f"got {ratio}")

    check("unknown sleeve raises",
          _raises(lambda: policy.sleeve("P9")))

    # Ceiling is the LOWER of 5% of risk base and 25% of the sleeve.
    check("P1 ceiling is the 5% rule (1000 < 3692)",
          policy.position_ceiling(policy.sleeve("P1")) == 1_000.0)
    p2_ceiling = round(policy.position_ceiling(policy.sleeve("P2")), 2)
    check("P2 ceiling is the sleeve rule (279.45 < 1000)",
          p2_ceiling == 279.45, f"got {p2_ceiling}")
    p6_ceiling = round(policy.position_ceiling(policy.sleeve("P6")), 2)
    check("P6 own-ideas ceiling is 500 — half a P1 position",
          p6_ceiling == 500.0, f"got {p6_ceiling}")


def test_recommendation_is_not_simply_the_maximum():
    """A cap is a ceiling, not a target — the default proposal is an equal
    weight within the sleeve."""
    print("Recommended size")
    policy = verify.Policy(risk_base=20_000.0)
    p4 = policy.sleeve("P4")
    sizing = verify.compute_sizing(100.0, make_account(), policy, p4)
    # P4 budget 2624.40 / 3 positions = 874.80; ceiling = min(1000, 656.10)
    check("P4 ceiling 656.10", sizing["ceiling"] == 656.1,
          f"got {sizing['ceiling']}")
    check("recommended clipped to ceiling", sizing["recommended"] == 656.1)

    p2 = policy.sleeve("P2")
    s2 = verify.compute_sizing(50.0, make_account(), policy, p2)
    # P2 budget 1117.80 / 6 = 186.30, below the 279.45 ceiling
    check("P2 recommends 186.30, below its ceiling", s2["recommended"] == 186.3,
          f"got {s2['recommended']}")
    check("not user_set when no budget given", s2["user_set"] is False)


def test_budget_ceiling_and_override():
    print("Budget ceiling / override")
    at = run(budget=1_000.0)
    check("budget at the ceiling passes",
          "position_budget" in names(at.checks, "pass"),
          f"blocks={[c.name for c in at.blocks]}")

    under = run(budget=400.0)
    check("budget under the ceiling passes",
          "position_budget" in names(under.checks, "pass"))
    check("uses my number, not the recommendation",
          under.sizing["chosen"] == 400.0 and under.sizing["user_set"] is True)

    over = run(budget=1_500.0)
    check("budget over the ceiling blocks",
          "over_cap" in names(over.checks, "block"),
          f"blocks={[c.name for c in over.blocks]}")
    check("verdict is DISAGREE, not WAIT", over.verdict == "DISAGREE",
          f"got {over.verdict!r}")
    over_check = next(c for c in over.blocks if c.name == "over_cap")
    check("explains it is not a refusal",
          "not a refusal" in (over_check.detail or ""))
    check("says it will be logged as an override",
          "override" in (over_check.detail or "").lower())

    none_given = run()
    check("no budget given -> info, awaiting approval",
          "position_budget" in names(none_given.checks, "info"))


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
    """The headline check: he bought lower, price ran, trade is different."""
    print("Drift past his entry")
    brief = run(call=make_call(call_price=round(LAST / 1.20, 2)))  # ~+20%
    check("drift raises a block", "drift" in names(brief.checks, "block"))
    check("verdict is WAIT", brief.verdict == "WAIT", f"got {brief.verdict!r}")
    check("reason mentions re-checking on a pullback",
          "pull" in brief.reason.lower())

    small = run(call=make_call(call_price=round(LAST / 1.03, 2)))  # ~+3%
    check("3% drift does not block", "drift" not in names(small.checks, "block"))


def test_tiered_drift_by_sleeve_and_source():
    """The same ~15% gap is a blocker against a dated P4 trade and acceptable
    against a P1 average cost. That distinction is the whole point of tiering."""
    print("Tiered drift")
    gap_price = round(LAST / 1.15, 2)  # ~+15%

    p1_holdings = run(call=make_call(call_price=gap_price, sleeve="P1",
                                     source_type="holdings"))
    check("15% vs P1 average cost does not block (limit 20)",
          "drift" not in names(p1_holdings.checks, "block"),
          f"blocks={[c.name for c in p1_holdings.blocks]}")

    p1_transaction = run(call=make_call(call_price=gap_price, sleeve="P1",
                                        source_type="transaction"))
    check("same gap blocks as a dated transaction (limit 5)",
          "drift" in names(p1_transaction.checks, "block"))

    p4_holdings = run(call=make_call(call_price=gap_price, sleeve="P4",
                                     source_type="holdings"))
    check("P4 holdings uses its own tight 5% limit",
          "drift" in names(p4_holdings.checks, "block"))

    check("wording says 'average cost' for holdings",
          any("average cost" in c.message for c in p1_holdings.checks
              if c.name == "drift"))
    check("rejects an unknown source_type",
          _raises(lambda: run(call=make_call(source_type="rumour"))))


def test_p5_is_structurally_untradeable():
    print("P5 options sleeve")
    brief = run(call=make_call(sleeve="P5"))
    check("P5 blocks", "sleeve_untradeable" in names(brief.checks, "block"))
    check("verdict is DISAGREE", brief.verdict == "DISAGREE")
    blocker = next(c for c in brief.blocks if c.name == "sleeve_untradeable")
    check("says it is structural, not a judgement",
          "structural" in (blocker.detail or "").lower())


def test_sleeve_position_count_and_budget():
    print("Sleeve limits")
    full = [{"ticker": f"T{i}", "shares": 1, "market_value": 100.0,
             "sector": "Technology", "sleeve": "P4"} for i in range(3)]
    brief = run(call=make_call(sleeve="P4"),
                account=make_account(positions=full, buying_power=5_000.0))
    check("full P4 sleeve blocks a 4th name",
          "sleeve_count" in names(brief.checks, "block"),
          f"blocks={[c.name for c in brief.blocks]}")

    # P2 budget is 1242; 1100 already used leaves 142 headroom.
    heavy = [{"ticker": "X", "shares": 1, "market_value": 1_100.0,
              "sector": "Technology", "sleeve": "P2"}]
    over = run(call=make_call(sleeve="P2"),
               account=make_account(positions=heavy, buying_power=5_000.0),
               budget=300.0)
    check("exceeding sleeve budget blocks",
          "sleeve_budget" in names(over.checks, "block"),
          f"blocks={[c.name for c in over.blocks]}")
    budget_block = next(c for c in over.blocks if c.name == "sleeve_budget")
    check("tells me not to borrow from another sleeve",
          "another sleeve" in (budget_block.detail or ""))

    clean = run(call=make_call(sleeve="P2"), budget=200.0)
    check("empty sleeve has full headroom",
          "sleeve_budget" in names(clean.checks, "pass"))


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
    """The overall cap is derived from the sleeves (14+3+6+4=27), not a flat 10
    — a hardcoded 10 would have contradicted P1's own limit of 14, and it would
    have gone stale again the moment P6 was added."""
    print("Overall position cap")
    policy = verify.Policy()
    expected = sum(s.max_positions for s in policy.sleeves.values()
                   if s.tradeable)
    check("total derived from tradeable sleeves, not hardcoded",
          policy.total_max_positions == expected == 27,
          f"got {policy.total_max_positions}, expected {expected}")

    ten = [{"ticker": f"S{i}", "shares": 1, "market_value": 10.0,
            "sector": "Energy", "sleeve": "P1"} for i in range(10)]
    ok = run(account=make_account(positions=ten, buying_power=5_000.0))
    check("10 positions no longer blocks overall",
          "position_count" not in names(ok.checks, "block"))

    full = [{"ticker": f"S{i}", "shares": 1, "market_value": 10.0,
             "sector": "Energy", "sleeve": "P2"} for i in range(expected)]
    brief = run(account=make_account(positions=full, buying_power=5_000.0))
    check(f"{expected} positions blocks a new name",
          "position_count" in names(brief.checks, "block"),
          f"blocks={[c.name for c in brief.blocks]}")
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


def test_his_direction_warning():
    """The MU lesson: his last action being a sell must lead the brief, not
    hide in the transaction log."""
    print("His direction")
    recent_sell = make_call(his_last_action={
        "action": "Satış", "date": dt.date.today().isoformat(),
        "price": 850.0, "note": "rebalancing weight"})
    brief = run(call=recent_sell)
    check("recent sell warns", "his_direction" in names(brief.checks, "warn"),
          f"warns={names(brief.checks,'warn')}")
    warning = next(c for c in brief.checks if c.name == "his_direction")
    check("quotes his stated reason", "rebalancing weight" in (warning.detail or ""))

    old_sell = make_call(his_last_action={
        "action": "sell",
        "date": (dt.date.today() - dt.timedelta(days=120)).isoformat()})
    stale = run(call=old_sell)
    check("120-day-old sell is info, not warn",
          "his_direction" in names(stale.checks, "info"))

    buy = run(call=make_call(his_last_action={
        "action": "Alım", "date": dt.date.today().isoformat()}))
    check("recent buy passes", "his_direction" in names(buy.checks, "pass"))

    missing = run()
    check("absent -> listed as unverified, not assumed fine",
          any("last action" in u for u in missing.unverified))


def test_manageability_warning():
    """The MU lesson generalised: a ceiling that buys <3 whole shares leaves
    only all-or-nothing position management."""
    print("Manageability")
    pricey = make_bars(count=300, start=900.0, drift=0.05, noise=1.0, seed=3)
    last = float(pricey["close"].iloc[-1])
    call = make_call(call_price=round(last, 2),
                     invalidation=round(last * 0.85, 2))

    frac = make_snapshot(bars=pricey,
                         tradability=verify.Tradability(tradable=True,
                                                        fractional=True))
    brief = verify.verify(call, frac, make_account(buying_power=5_000.0),
                          verify.Policy(), None)
    check("1-share ceiling warns", "manageability" in names(brief.checks, "warn"),
          f"warns={names(brief.checks,'warn')}")
    warning = next(c for c in brief.checks if c.name == "manageability")
    check("mentions the fractional alternative and its cost",
          "market-order-only" in (warning.detail or ""))

    no_frac = make_snapshot(bars=pricey,
                            tradability=verify.Tradability(tradable=True,
                                                           fractional=False))
    hard = verify.verify(call, no_frac, make_account(buying_power=5_000.0),
                         verify.Policy(), None)
    hard_warning = next(c for c in hard.checks if c.name == "manageability")
    check("says plainly when fractional is unavailable",
          "NOT available" in (hard_warning.detail or ""))

    # Boundary: ceiling 1000 / price 333 = exactly 3 shares -> no warning
    cheap = make_bars(count=300, start=333.0, drift=0.0, noise=0.0, seed=4)
    last3 = float(cheap["close"].iloc[-1])
    ok = verify.verify(make_call(call_price=round(last3, 2),
                                 invalidation=round(last3 * 0.85, 2)),
                       make_snapshot(bars=cheap),
                       make_account(buying_power=5_000.0),
                       verify.Policy(), None)
    check("exactly 3 shares does not warn",
          "manageability" not in names(ok.checks, "warn"),
          f"warns={names(ok.checks,'warn')}")


def test_screen_source_type():
    """Independent ideas: P6 sleeve, tight drift from the identified level, and
    no his-direction check because he has no opinion on them."""
    print("Screen source type")
    call = make_call(sleeve="P6", source_type="screen")
    call.pop("his_last_action", None)
    brief = run(call=call, account=make_account(buying_power=5_000.0))
    check("P6 screen idea is accepted", brief.sleeve == "P6"
          and brief.source_type == "screen")
    check("no his_direction check for own ideas",
          not any(c.name == "his_direction" for c in brief.checks))
    check("no his-action unverified noise",
          not any("last action" in u for u in brief.unverified))

    # Screen uses the strict transaction drift limit, not P6's holdings value.
    policy = verify.Policy()
    p6 = policy.sleeve("P6")
    check("screen drift limit is the strict one",
          policy.drift_limit(p6, "screen") == policy.transaction_drift_pct)

    drifted = run(call=make_call(sleeve="P6", source_type="screen",
                                 call_price=round(LAST / 1.10, 2)),
                  account=make_account(buying_power=5_000.0))
    check("10% past the screen's entry level blocks",
          "drift" in names(drifted.checks, "block"))

    check("unknown source_type still rejected",
          _raises(lambda: run(call=make_call(source_type="vibes"))))


def test_existing_exposure_across_accounts():
    """The blind spot a real cross-account read exposed: 18% of net worth sat
    in NVDA in a personal account, invisible to the sleeve checks. Mirroring it
    would have been sized as a fresh position."""
    print("Cross-account exposure")
    external = [{"ticker": "TEST", "market_value": 8_858.22,
                 "sector": "Technology", "account": "individual"}]
    acct = make_account(buying_power=5_000.0,
                        external_positions=external,
                        total_portfolio_value=49_158.75)
    brief = run(account=acct)
    check("warns when already held elsewhere",
          "existing_exposure" in names(brief.checks, "warn"),
          f"warns={names(brief.checks,'warn')}")
    warning = next(c for c in brief.checks if c.name == "existing_exposure")
    check("names the account it is held in",
          "individual" in (warning.detail or ""))
    check("says it concentrates rather than diversifies",
          "does not diversify" in (warning.detail or ""))

    # Above 25% of net worth it blocks outright.
    heavy = make_account(buying_power=5_000.0,
                         external_positions=[{"ticker": "TEST",
                                              "market_value": 14_000.0,
                                              "sector": "Technology",
                                              "account": "individual"}],
                         total_portfolio_value=49_158.75)
    blocked = run(account=heavy)
    check("blocks past 25% of net worth",
          "existing_exposure" in names(blocked.checks, "block"))

    none_held = run(account=make_account(
        buying_power=5_000.0,
        external_positions=[{"ticker": "OTHER", "market_value": 5_000.0,
                             "sector": "Energy", "account": "individual"}],
        total_portfolio_value=49_158.75))
    check("passes when not held elsewhere",
          "existing_exposure" in names(none_held.checks, "pass"))

    silent = run()
    check("absent data is unverified, not assumed zero",
          any("outside the agentic" in u for u in silent.unverified))


def test_concentration_spans_all_accounts():
    print("Cross-account concentration")
    external = [{"ticker": f"T{i}", "market_value": 4_000.0,
                 "sector": "Technology", "account": "individual"}
                for i in range(4)]
    acct = make_account(buying_power=5_000.0, external_positions=external,
                        total_portfolio_value=49_158.75)
    brief = run(account=acct)
    check("sector concentration counts external holdings",
          "concentration" in names(brief.checks, "warn"),
          f"warns={names(brief.checks,'warn')}")
    conc = next(c for c in brief.checks if c.name == "concentration")
    check("measured against net worth, not the risk base",
          "net worth" in conc.message)


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
    check("shows the sleeve and ceiling",
          "Sleeve" in text and "ceiling" in text)
    check("asks for approval rather than asserting a size",
          "name a different one" in text)
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
