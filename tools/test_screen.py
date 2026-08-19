"""Tests for screen.py — constructed price paths with known properties.

Each fixture is built so the expected outcome follows from the construction,
not from eyeballing a chart.

Run:  .venv/bin/python tools/test_screen.py
"""

from __future__ import annotations

import datetime as dt
import sys

import numpy as np
import pandas as pd

import screen

FAILURES: list[str] = []
TODAY = dt.date(2026, 8, 15)


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label} {detail}")
        FAILURES.append(label)


def frame(closes, volume=5_000_000) -> pd.DataFrame:
    closes = np.asarray(closes, dtype=float)
    return pd.DataFrame({
        "open": closes, "high": closes + 0.5, "low": closes - 0.5,
        "close": closes, "volume": [volume] * len(closes),
    }, index=pd.date_range("2025-01-01", periods=len(closes), freq="B"))


def rising(n: int = 250, amp: float = 5.0, period: float = 10.0) -> np.ndarray:
    """A rising trend WITH oscillation.

    The oscillation is not decoration. A perfectly linear ramp has no local
    extrema, so pivot detection finds no support or resistance at all — a
    straight line is not a chart, and a fixture made of one tests nothing.
    """
    i = np.arange(n)
    return np.linspace(50, 150, n) + amp * np.sin(i / period)


def uptrend_then_pullback() -> np.ndarray:
    """Rising and oscillating, then a shallow 6-bar dip of ~4%.

    Shallow is the point: it leaves RSI near 47 (inside the 35-50 band) and
    price just above a support the earlier oscillation established. A steeper
    dip drives RSI into the teens, which is a breakdown rather than a pullback
    and the screen correctly rejects it."""
    rise = rising(250, amp=5.0, period=10.0)
    top = rise[-1]
    return np.concatenate([rise, np.linspace(top, top * 0.96, 6)])


def uptrend_near_highs() -> np.ndarray:
    """Rising and oscillating, ending above its 50-day — what the earnings
    screen requires, since it wants a name in shape ahead of the print."""
    rise = rising(250, amp=8.0, period=10.0)
    top = rise[-1]
    return np.concatenate([rise, np.linspace(top, top * 0.99, 3)])


def universe(closes=None, **overrides) -> screen.Universe:
    closes = uptrend_then_pullback() if closes is None else closes
    bars = frame(closes)
    defaults = dict(
        ticker="TEST", bars=bars, price=float(bars["close"].iloc[-1]),
        sector="Electronic Technology", market_cap=50e9, avg_volume=5_000_000,
        next_earnings=TODAY + dt.timedelta(days=25),
    )
    defaults.update(overrides)
    return screen.Universe(**defaults)


CFG = {
    "min_price": 5.0, "min_avg_volume": 1_000_000,
    "pullback": {"rsi_low": 35.0, "rsi_high": 50.0, "max_pct_above_support": 6.0},
    "earnings_catalyst": {"min_days": 14, "max_days": 42},
    "relative_strength": {"lookbacks_days": [21, 63, 126],
                          "min_periods_outperforming": 2},
    "bora_adjacent": {"sectors": ["Electronic Technology"],
                      "min_market_cap": 2_000_000_000},
}


# --------------------------------------------------------------------------

def test_pullback_requires_trend_intact():
    print("Pullback: trend must be intact")
    downtrend = np.linspace(150, 50, 260)
    hit = screen.pullback_in_uptrend(universe(downtrend), CFG["pullback"])
    check("no hit below the 200-day", hit is None)

    straight_up = rising(260)
    hit = screen.pullback_in_uptrend(universe(straight_up), CFG["pullback"])
    check("no hit when RSI is hot (no pullback yet)", hit is None)


def test_pullback_entry_reference_is_support_not_price():
    """The entry reference must be the level being reasoned about, so drift is
    measured from the trigger rather than from wherever price drifts to."""
    print("Pullback: entry reference")
    u = universe()
    hit = screen.pullback_in_uptrend(u, CFG["pullback"])
    if hit is None:
        check("fixture produces a hit", False, "no hit — fixture needs adjusting")
        return
    check("fixture produces a hit", True)
    check("entry reference is below current price",
          hit.entry_reference < u.price,
          f"ref={hit.entry_reference} price={u.price}")
    check("entry reference equals the support level",
          hit.entry_reference == hit.evidence["support"])
    check("invalidation sits below support",
          hit.invalidation_hint < hit.entry_reference)
    check("evidence carries the numbers behind the claim",
          {"rsi14", "sma200", "support", "pct_above_support"} <= set(hit.evidence))
    check("rationale names the support and RSI",
          "support" in hit.rationale and "RSI" in hit.rationale)


def test_earnings_window():
    print("Earnings catalyst window")
    inside = screen.earnings_catalyst(universe(uptrend_near_highs()),
                                      CFG["earnings_catalyst"], TODAY)
    check("25 days out hits", inside is not None)
    if inside:
        check("days_out recorded", inside.evidence["days_out"] == 25)
        check("rationale refuses to predict the print",
              "coin flip" in inside.rationale or "not a prediction" in inside.rationale)

    too_soon = screen.earnings_catalyst(
        universe(uptrend_near_highs(),
                 next_earnings=TODAY + dt.timedelta(days=3)),
        CFG["earnings_catalyst"], TODAY)
    check("3 days out is excluded (blackout)", too_soon is None)

    too_far = screen.earnings_catalyst(
        universe(uptrend_near_highs(),
                 next_earnings=TODAY + dt.timedelta(days=90)),
        CFG["earnings_catalyst"], TODAY)
    check("90 days out is excluded", too_far is None)

    unknown = screen.earnings_catalyst(
        universe(uptrend_near_highs(), next_earnings=None),
        CFG["earnings_catalyst"], TODAY)
    check("unknown earnings date does not hit", unknown is None)

    # A deep decline puts price under its 50-day. The window alone is not
    # enough — the screen wants a name in shape ahead of the print.
    rise = rising(250)
    deep = np.concatenate([rise, np.linspace(rise[-1], rise[-1] * 0.80, 25)])
    weak = screen.earnings_catalyst(universe(deep), CFG["earnings_catalyst"], TODAY)
    check("a name below its 50-day does not hit even in the window",
          weak is None)


def test_relative_strength_needs_real_outperformance():
    print("Relative strength")
    strong = frame(rising(260))
    flat = frame(np.linspace(100, 101, 260))
    hit = screen.relative_strength(universe(rising(260)), flat,
                                   CFG["relative_strength"])
    check("beats a flat benchmark", hit is not None)
    if hit:
        check("records excess per lookback",
              hit.evidence["21d_excess_pct"] > 0
              and hit.evidence["periods_beaten"] == 3)
        check("rationale admits momentum fails at turns",
              "fails at turns" in hit.rationale)

    laggard = screen.relative_strength(universe(np.linspace(100, 101, 260)),
                                       strong, CFG["relative_strength"])
    check("underperformer does not hit", laggard is None)


def test_bora_adjacent_bands():
    print("Bora-adjacent bands")
    hit = screen.bora_adjacent(universe(rising(260)),
                               CFG["bora_adjacent"])
    check("matching sector and cap hits", hit is not None)
    if hit:
        check("rationale states the correlation cost",
              "correlated" in hit.rationale)

    wrong_sector = screen.bora_adjacent(
        universe(rising(260), sector="Health Services"),
        CFG["bora_adjacent"])
    check("wrong sector excluded", wrong_sector is None)

    small = screen.bora_adjacent(
        universe(rising(260), market_cap=100e6),
        CFG["bora_adjacent"])
    check("below the cap floor excluded", small is None)


def test_liquidity_and_price_floors():
    print("Floors")
    cheap = screen.run_screens([universe(price=2.0)], CFG, today=TODAY)
    check("sub-$5 filtered out entirely", cheap == [])

    thin = screen.run_screens([universe(avg_volume=1_000)], CFG, today=TODAY)
    check("illiquid filtered out entirely", thin == [])


def test_candidates_always_land_in_p6():
    """An independent idea must never enter a sleeve that mirrors his book."""
    print("Sleeve isolation")
    hits = screen.run_screens([universe()], CFG, today=TODAY)
    check("produced at least one hit", len(hits) > 0)
    for hit in hits:
        cand = hit.to_candidate()
        check(f"{hit.strategy} -> P6", cand["sleeve"] == "P6")
        check(f"{hit.strategy} -> source screener", cand["source"] == "screener")
        check(f"{hit.strategy} -> source_type screen",
              cand["source_type"] == "screen")
        check(f"{hit.strategy} carries a rationale",
              bool(cand["rationale"]))
        check(f"{hit.strategy} entry ref becomes call_price",
              cand["call_price"] == hit.entry_reference)


def test_render_disclaims():
    print("Rendering")
    hits = screen.run_screens([universe()], CFG, today=TODAY)
    text = screen.render(hits)
    check("states no thesis from Bora", "NO thesis from Bora" in text)
    check("calls a hit a question not an answer", "not an answer" in text)
    empty = screen.render([])
    check("empty is a normal outcome, not an error",
          "normal outcome" in empty)


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S): {', '.join(FAILURES)}")
        return 1
    print(f"All {len(tests)} screen test groups passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
