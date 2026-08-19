"""Independent idea generation — four named screens over a candidate universe.

**What this is.** A systematic filter that surfaces names for verification, each
with a named strategy, a stated rationale, a specific entry reference, and an
invalidation hint. Everything is auditable and reproducible.

**What this is not.** Alpha. A screen anyone can run over public data has no
edge in itself. Its value is narrowing a universe to things worth checking, and
every hit still goes through the same `/bora-check` verification as one of
Bora's names. A screen hit is a question, not an answer.

Two properties that keep it honest:

  - Every hit carries `evidence` — the actual numbers that made it match — so a
    claim can be checked rather than taken on faith.
  - Every hit carries `entry_reference`: the specific level the screen is
    reasoning about. Drift is measured from there, so a setup that has already
    run away from its own trigger is caught by the normal drift rule instead of
    being quietly taken at any price.

Offline. No network, no broker access.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from dataclasses import dataclass, field

import pandas as pd

import indicators
from market_data import parse_market


@dataclass
class ScreenHit:
    ticker: str
    strategy: str
    rationale: str
    entry_reference: float
    invalidation_hint: float | None = None
    evidence: dict = field(default_factory=dict)

    def to_candidate(self, extra: dict | None = None) -> dict:
        """Shape rank.py and verify.py consume. Sleeve is always P6: an
        independent idea must never land in a sleeve that mirrors his book."""
        cand = {
            "ticker": self.ticker,
            "sleeve": "P6",
            "source": "screener",
            "source_type": "screen",
            "strategy": self.strategy,
            "rationale": self.rationale,
            "his_avg_cost": self.entry_reference,
            "call_price": self.entry_reference,
            "invalidation": self.invalidation_hint,
            "evidence": self.evidence,
        }
        cand.update(extra or {})
        return cand


@dataclass
class Universe:
    """One symbol's data, assembled by the agent from Robinhood."""
    ticker: str
    bars: pd.DataFrame
    price: float
    sector: str | None = None
    market_cap: float | None = None
    avg_volume: float | None = None
    next_earnings: dt.date | None = None


# --------------------------------------------------------------------------
# Screens
# --------------------------------------------------------------------------

def pullback_in_uptrend(u: Universe, cfg: dict) -> ScreenHit | None:
    """A strong name that has pulled back to support rather than broken down.

    Requires the long-term trend intact (above the 200-day), momentum cooled but
    not collapsed, and price near a real support level. The entry reference is
    that support level, not today's price — if it bounces hard before you act,
    the drift rule should stop you chasing it.
    """
    read = indicators.analyze(u.bars, u.ticker)
    if read.sma200 is None or read.rsi14 is None:
        return None
    if read.last_close <= read.sma200:
        return None

    lo, hi = cfg.get("rsi_low", 35.0), cfg.get("rsi_high", 50.0)
    if not (lo <= read.rsi14 <= hi):
        return None

    supports = [lv for lv in read.levels
                if lv.kind == "support" and lv.price < read.last_close]
    if not supports:
        return None
    support = max(supports, key=lambda lv: lv.price)

    above = (read.last_close - support.price) / support.price * 100.0
    if above > cfg.get("max_pct_above_support", 6.0):
        return None

    return ScreenHit(
        ticker=u.ticker,
        strategy="pullback_in_uptrend",
        rationale=(
            f"Above its 200-day ({read.sma200:.2f}) so the long trend is intact, "
            f"but RSI has cooled to {read.rsi14:.0f} and price is {above:.1f}% "
            f"above support at {support.price:.2f} ({support.touches} touches). "
            f"A pullback in an uptrend, not a breakdown — provided that support holds."
        ),
        entry_reference=round(support.price, 2),
        invalidation_hint=round(support.price * 0.97, 2),
        evidence={"rsi14": round(read.rsi14, 1), "sma200": round(read.sma200, 2),
                  "support": round(support.price, 2),
                  "support_touches": support.touches,
                  "pct_above_support": round(above, 2),
                  "trend": read.trend},
    )


def earnings_catalyst(u: Universe, cfg: dict,
                      today: dt.date | None = None) -> ScreenHit | None:
    """A dated event to trade around, outside the blackout.

    The point is not that earnings are predictable — they are not. It is that a
    scheduled catalyst gives the position a natural review date, which is more
    discipline than an open-ended hold.
    """
    today = today or dt.date.today()
    if not u.next_earnings:
        return None
    days = (u.next_earnings - today).days
    if not (cfg.get("min_days", 14) <= days <= cfg.get("max_days", 42)):
        return None

    read = indicators.analyze(u.bars, u.ticker)
    if read.sma50 is None or read.last_close <= read.sma50:
        return None

    return ScreenHit(
        ticker=u.ticker,
        strategy="earnings_catalyst",
        rationale=(
            f"Earnings on {u.next_earnings} — {days} days out, well outside the "
            f"3-day blackout, and price is above its 50-day ({read.sma50:.2f}). "
            f"The print is a dated review point, not a prediction: earnings are a "
            f"coin flip and this is only a reason to have a decision date."
        ),
        entry_reference=round(read.last_close, 2),
        invalidation_hint=round(read.sma50, 2),
        evidence={"next_earnings": u.next_earnings.isoformat(), "days_out": days,
                  "sma50": round(read.sma50, 2), "trend": read.trend},
    )


def relative_strength(u: Universe, benchmark: pd.DataFrame,
                      cfg: dict) -> ScreenHit | None:
    """Outperforming the benchmark across multiple lookbacks.

    Momentum works in trending tapes and fails at turns. Worth remembering that
    the trader this system follows is currently a net seller, which is a
    turn-ish signal — so this screen is the one to trust least right now.
    """
    lookbacks = cfg.get("lookbacks_days", [21, 63, 126])
    needed = cfg.get("min_periods_outperforming", 2)

    close = indicators._normalize_columns(u.bars)["close"]
    bench = indicators._normalize_columns(benchmark)["close"]
    if len(close) < max(lookbacks) + 1 or len(bench) < max(lookbacks) + 1:
        return None

    beats, detail = 0, {}
    for days in lookbacks:
        mine = (close.iloc[-1] - close.iloc[-days - 1]) / close.iloc[-days - 1] * 100.0
        theirs = (bench.iloc[-1] - bench.iloc[-days - 1]) / bench.iloc[-days - 1] * 100.0
        excess = mine - theirs
        detail[f"{days}d_excess_pct"] = round(excess, 2)
        if excess > 0:
            beats += 1

    if beats < needed:
        return None

    read = indicators.analyze(u.bars, u.ticker)
    return ScreenHit(
        ticker=u.ticker,
        strategy="relative_strength",
        rationale=(
            f"Beat the benchmark on {beats} of {len(lookbacks)} lookbacks "
            f"({', '.join(f'{k}: {v:+.1f}%' for k, v in detail.items())}). "
            f"Momentum — which works in a trending tape and fails at turns."
        ),
        entry_reference=round(read.last_close, 2),
        invalidation_hint=round(read.sma50, 2) if read.sma50 else None,
        evidence={**detail, "periods_beaten": beats, "trend": read.trend},
    )


def bora_adjacent(u: Universe, cfg: dict) -> ScreenHit | None:
    """Matches the shape of his book without being one of his picks.

    Bands come from his actual holdings, not a guess. This is the screen closest
    to his style — and therefore the one most likely to add correlated exposure
    to a book that is already concentrated. That is a cost, not a feature.
    """
    sectors = [s.lower() for s in cfg.get("sectors", [])]
    if sectors and (u.sector or "").lower() not in sectors:
        return None
    if u.market_cap is not None and u.market_cap < cfg.get("min_market_cap", 0):
        return None

    read = indicators.analyze(u.bars, u.ticker)
    if read.sma200 is None or read.last_close <= read.sma200:
        return None

    return ScreenHit(
        ticker=u.ticker,
        strategy="bora_adjacent",
        rationale=(
            f"{u.sector}, market cap {u.market_cap/1e9:.1f}B, above its 200-day — "
            f"the shape of his book without being one of his names. Note this "
            f"screen adds correlated exposure to an already-concentrated theme."
        ),
        entry_reference=round(read.last_close, 2),
        invalidation_hint=round(read.sma200, 2),
        evidence={"sector": u.sector, "market_cap": u.market_cap,
                  "sma200": round(read.sma200, 2), "trend": read.trend},
    )


# --------------------------------------------------------------------------
# Pipeline
# --------------------------------------------------------------------------

def run_screens(universe: list[Universe], config: dict,
                benchmark: pd.DataFrame | None = None,
                strategies: list[str] | None = None,
                today: dt.date | None = None) -> list[ScreenHit]:
    strategies = strategies or ["pullback_in_uptrend", "earnings_catalyst",
                               "relative_strength", "bora_adjacent"]
    min_price = config.get("min_price", 5.0)
    min_volume = config.get("min_avg_volume", 1_000_000)

    hits: list[ScreenHit] = []
    for u in universe:
        # Liquidity and price floors first — a setup you cannot trade cleanly
        # is not a setup.
        if u.price < min_price:
            continue
        if u.avg_volume is not None and u.avg_volume < min_volume:
            continue

        if "pullback_in_uptrend" in strategies:
            hit = pullback_in_uptrend(u, config.get("pullback", {}))
            if hit:
                hits.append(hit)
        if "earnings_catalyst" in strategies:
            hit = earnings_catalyst(u, config.get("earnings_catalyst", {}), today)
            if hit:
                hits.append(hit)
        if "relative_strength" in strategies and benchmark is not None:
            hit = relative_strength(u, benchmark, config.get("relative_strength", {}))
            if hit:
                hits.append(hit)
        if "bora_adjacent" in strategies:
            hit = bora_adjacent(u, config.get("bora_adjacent", {}))
            if hit:
                hits.append(hit)

    hits.sort(key=lambda h: (h.strategy, h.ticker))
    return hits


def render(hits: list[ScreenHit]) -> str:
    lines: list[str] = []
    add = lines.append
    add("=" * 72)
    add(f"  INDEPENDENT SCREENS — {len(hits)} hit(s)")
    add("=" * 72)
    add("  These matched a filter. They carry NO thesis from Bora and no track")
    add("  record. A hit is a question for verification, not an answer.")
    add("")

    by_strategy: dict[str, list[ScreenHit]] = {}
    for hit in hits:
        by_strategy.setdefault(hit.strategy, []).append(hit)

    for strategy, group in by_strategy.items():
        add(f"{strategy.upper().replace('_', ' ')} ({len(group)})")
        add("-" * 72)
        for hit in group:
            add(f"  {hit.ticker:<6} entry ref {hit.entry_reference:,.2f}"
                + (f"   invalidation ~{hit.invalidation_hint:,.2f}"
                   if hit.invalidation_hint else ""))
            for line in _wrap(hit.rationale, 66):
                add(f"         {line}")
            add("")
    if not hits:
        add("  Nothing matched today. That is a normal outcome, not a failure.")
    return "\n".join(lines)


def _wrap(text: str, width: int) -> list[str]:
    import textwrap
    return textwrap.wrap(text, width=width)


def load_universe(bars_dir: str, tickers: list[str],
                  meta: dict) -> list[Universe]:
    out: list[Universe] = []
    for ticker in tickers:
        try:
            with open(f"{bars_dir}/{ticker}.json", encoding="utf-8") as handle:
                snap = parse_market(json.load(handle))
        except (OSError, json.JSONDecodeError, ValueError):
            continue
        info = meta.get(ticker, {})
        earnings = info.get("next_earnings")
        out.append(Universe(
            ticker=ticker,
            bars=snap.bars,
            price=snap.last_price,
            sector=info.get("sector"),
            market_cap=info.get("market_cap"),
            avg_volume=info.get("avg_volume"),
            next_earnings=dt.date.fromisoformat(earnings[:10]) if earnings else None,
        ))
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run independent screens over a candidate universe.")
    parser.add_argument("--bars-dir", required=True)
    parser.add_argument("--meta", required=True,
                        help="JSON: {TICKER: {sector, market_cap, avg_volume, next_earnings}}")
    parser.add_argument("--benchmark", default="QQQ")
    parser.add_argument("--strategies", default="")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    from verify import _load_risk_profile
    config = _load_risk_profile().get("screens", {})

    try:
        with open(args.meta, encoding="utf-8") as handle:
            meta = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Error reading meta: {exc}", file=sys.stderr)
        return 2

    tickers = [t for t in meta if t != args.benchmark]
    universe = load_universe(args.bars_dir, tickers, meta)

    benchmark = None
    try:
        with open(f"{args.bars_dir}/{args.benchmark}.json", encoding="utf-8") as h:
            benchmark = parse_market(json.load(h)).bars
    except (OSError, json.JSONDecodeError, ValueError):
        print(f"note: no benchmark bars for {args.benchmark} — "
              f"relative_strength skipped", file=sys.stderr)

    strategies = [s.strip() for s in args.strategies.split(",") if s.strip()] or None
    hits = run_screens(universe, config, benchmark, strategies)

    if args.json:
        print(json.dumps([h.to_candidate() for h in hits], indent=2, default=str))
    else:
        print(render(hits))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
