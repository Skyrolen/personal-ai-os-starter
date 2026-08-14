"""Market data structures.

All market data comes from Robinhood's MCP server — the same venue we execute
on, which makes its tradability and liquidity data authoritative in a way no
third-party feed can be. The agent gathers it and writes `market.json`; this
module parses that file into typed structures for `verify.py`.

**Nothing here touches the network.** The seam is deliberate: the agent fetches
(and needs credentials to do so), Python does arithmetic (and needs nothing).
That keeps the policy math deterministic, testable offline, and impossible to
run against accidentally-stale live state.

Expected `market.json`, assembled from these MCP calls:

    get_equity_historicals    -> bars
    get_equity_quotes         -> quote
    get_equity_fundamentals   -> fundamentals (PE, market cap, avg volume, 52w)
    get_financials            -> fundamentals.revenue_growth
    get_earnings_results      -> fundamentals.next_earnings
    get_equity_tradability    -> tradability

    {
      "ticker": "NVDA",
      "as_of": "2026-08-14T20:00:00Z",
      "quote": {"last": 180.25, "bid": 180.20, "ask": 180.30},
      "bars": [
        {"begins_at": "2026-08-13", "open": 178.0, "high": 181.2,
         "low": 177.4, "close": 180.1, "volume": 41200000}
      ],
      "fundamentals": {
        "name": "NVIDIA", "sector": "Technology", "market_cap": 4.4e12,
        "trailing_pe": 52.1, "average_volume": 40000000,
        "revenue_growth": 0.62, "next_earnings": "2026-08-27"
      },
      "tradability": {"tradable": true, "fractional": true,
                      "sessions": ["regular", "extended"]}
    }

Every field except `ticker` and `bars` is optional. Anything absent is recorded
as unverified and surfaced in the brief, rather than silently treated as fine.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, field

import pandas as pd


class MarketDataError(RuntimeError):
    """Raised when market data is missing or unusable.

    Deliberately loud: a verification step that proceeds on empty data would
    produce a confident-looking brief backed by nothing.
    """


@dataclass
class Fundamentals:
    ticker: str
    name: str | None = None
    sector: str | None = None
    industry: str | None = None
    market_cap: float | None = None
    trailing_pe: float | None = None
    forward_pe: float | None = None
    price_to_sales: float | None = None
    revenue_growth: float | None = None
    earnings_growth: float | None = None
    debt_to_equity: float | None = None
    next_earnings: dt.date | None = None
    avg_volume_10d: float | None = None
    high_52_weeks: float | None = None
    low_52_weeks: float | None = None
    missing: list[str] = field(default_factory=list)


@dataclass
class Tradability:
    """From `get_equity_tradability`. Authoritative for this account: it knows
    about halts, restrictions, and fractional eligibility that volume alone
    cannot tell us."""
    tradable: bool | None = None
    fractional: bool | None = None
    sessions: list[str] = field(default_factory=list)
    reason: str | None = None


@dataclass
class Quote:
    last: float | None = None
    bid: float | None = None
    ask: float | None = None

    @property
    def spread_pct(self) -> float | None:
        """Bid/ask spread as a % of the midpoint — a truer liquidity read than
        average volume, because it is what you actually pay to cross."""
        if not self.bid or not self.ask or self.bid <= 0 or self.ask <= 0:
            return None
        mid = (self.bid + self.ask) / 2.0
        return (self.ask - self.bid) / mid * 100.0 if mid > 0 else None


@dataclass
class MarketSnapshot:
    """Everything verify.py needs about one symbol, at one moment."""
    ticker: str
    bars: pd.DataFrame
    fundamentals: Fundamentals
    tradability: Tradability = field(default_factory=Tradability)
    quote: Quote = field(default_factory=Quote)
    as_of: str | None = None

    @property
    def last_price(self) -> float:
        """Prefer the live quote; fall back to the last bar's close.

        The distinction matters for sizing: bars can lag by a session, and
        sizing off a stale price silently breaks the position cap.
        """
        if self.quote.last:
            return float(self.quote.last)
        return float(self.bars["close"].iloc[-1])


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------

def load_market_json(path: str) -> MarketSnapshot:
    """Read a market.json assembled by the agent from Robinhood MCP calls."""
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except FileNotFoundError as exc:
        raise MarketDataError(f"market.json not found at {path!r}") from exc
    except json.JSONDecodeError as exc:
        raise MarketDataError(f"market.json at {path!r} is not valid JSON: {exc}") from exc
    return parse_market(payload)


def parse_market(payload: dict) -> MarketSnapshot:
    ticker = str(payload.get("ticker", "")).strip().upper()
    if not ticker:
        raise MarketDataError("market.json must include a 'ticker'")

    bars = _parse_bars(payload.get("bars"), ticker)
    raw_fundamentals = payload.get("fundamentals") or {}

    return MarketSnapshot(
        ticker=ticker,
        bars=bars,
        fundamentals=_parse_fundamentals(ticker, raw_fundamentals),
        tradability=_parse_tradability(payload.get("tradability")),
        quote=_parse_quote(payload.get("quote")),
        as_of=payload.get("as_of"),
    )


def _parse_bars(raw, ticker: str) -> pd.DataFrame:
    if not raw:
        raise MarketDataError(
            f"No price bars for {ticker}. Fetch them with get_equity_historicals "
            f"(interval='day', at least ~250 sessions for a 200-day average)."
        )

    frame = pd.DataFrame(raw)
    frame.columns = [str(c).lower() for c in frame.columns]

    # Robinhood returns open_price/close_price on some endpoints.
    frame = frame.rename(columns={
        "open_price": "open", "high_price": "high",
        "low_price": "low", "close_price": "close",
    })

    required = {"open", "high", "low", "close"}
    missing = required - set(frame.columns)
    if missing:
        raise MarketDataError(
            f"Bars for {ticker} are missing columns: {sorted(missing)}. "
            f"Got: {sorted(frame.columns)}"
        )

    for column in ("open", "high", "low", "close", "volume"):
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")

    # Robinhood marks synthesized gap-fill bars; they carry no information and
    # would flatten indicators if left in.
    if "interpolated" in frame.columns:
        frame = frame[frame["interpolated"] != True]  # noqa: E712

    if "begins_at" in frame.columns:
        frame["begins_at"] = pd.to_datetime(frame["begins_at"], errors="coerce",
                                            utc=True)
        frame = frame.sort_values("begins_at").set_index("begins_at")

    frame = frame.dropna(subset=["close"])
    if frame.empty:
        raise MarketDataError(f"All bars for {ticker} were empty or interpolated")
    return frame


def _parse_fundamentals(ticker: str, raw: dict) -> Fundamentals:
    missing: list[str] = []

    def pick(*keys: str) -> float | None:
        for key in keys:
            value = raw.get(key)
            if value is not None and value != "":
                try:
                    return float(value)
                except (TypeError, ValueError):
                    continue
        missing.append(keys[0])
        return None

    next_earnings = _as_date(raw.get("next_earnings"))
    if next_earnings is None:
        missing.append("next_earnings")

    return Fundamentals(
        ticker=ticker,
        name=raw.get("name"),
        sector=raw.get("sector"),
        industry=raw.get("industry"),
        market_cap=pick("market_cap"),
        trailing_pe=pick("trailing_pe", "pe_ratio"),
        forward_pe=pick("forward_pe"),
        price_to_sales=pick("price_to_sales", "ps_ratio"),
        revenue_growth=pick("revenue_growth"),
        earnings_growth=pick("earnings_growth"),
        debt_to_equity=pick("debt_to_equity"),
        next_earnings=next_earnings,
        avg_volume_10d=pick("average_volume", "average_volume_2_weeks",
                            "avg_volume_10d"),
        high_52_weeks=pick("high_52_weeks"),
        low_52_weeks=pick("low_52_weeks"),
        missing=missing,
    )


def _parse_tradability(raw) -> Tradability:
    if not raw:
        return Tradability()
    if isinstance(raw, list):  # get_equity_tradability returns a list
        raw = raw[0] if raw else {}
    sessions = raw.get("sessions") or raw.get("eligible_sessions") or []
    if isinstance(sessions, str):
        sessions = [sessions]
    return Tradability(
        tradable=_as_bool(raw.get("tradable", raw.get("tradeable"))),
        fractional=_as_bool(raw.get("fractional",
                                    raw.get("fractional_eligible"))),
        sessions=list(sessions),
        reason=raw.get("reason") or raw.get("restriction_reason"),
    )


def _parse_quote(raw) -> Quote:
    if not raw:
        return Quote()
    if isinstance(raw, list):
        raw = raw[0] if raw else {}

    def num(*keys: str) -> float | None:
        for key in keys:
            value = raw.get(key)
            if value not in (None, ""):
                try:
                    return float(value)
                except (TypeError, ValueError):
                    continue
        return None

    return Quote(
        last=num("last", "last_trade_price", "price"),
        bid=num("bid", "bid_price"),
        ask=num("ask", "ask_price"),
    )


def _as_date(value) -> dt.date | None:
    if value in (None, ""):
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _as_bool(value) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("true", "yes", "1"):
            return True
        if lowered in ("false", "no", "0"):
            return False
    return None
