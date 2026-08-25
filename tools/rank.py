"""Rank candidates by TAKEABILITY — not by expected return.

The distinction matters and is load-bearing. This module answers "can I take
this cleanly today, at my size, under my rules?" It does not and cannot answer
"will this go up". Nothing here is a forecast, and a high score is not a
recommendation — it means the mechanical obstacles are low.

Design constraints:
  - Every score is a transparent sum of NAMED components, each carrying its own
    points and a one-line reason. A ranking you cannot audit is a ranking you
    should not act on.
  - Any component that would be a hard block in verify.py marks the candidate
    NOT TAKEABLE. A blocked name can still be listed (you may want to know it
    exists) but can never outrank a clean one.
  - Policy, Sleeve, and the drift/ceiling rules are imported from verify.py
    rather than reimplemented, so the ranker and the checker can never drift
    apart.

Offline: no network, no broker access. The agent gathers; this scores.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from dataclasses import dataclass, field

from verify import Policy, Sleeve, _load_risk_profile, _sleeves_from_profile


# Points below the blocking threshold are unreachable by any combination of
# positive components, so a blocked name can never sort above a clean one.
BLOCKED_PENALTY = -1000.0


@dataclass
class Component:
    name: str
    points: float
    reason: str
    blocking: bool = False


@dataclass
class Ranked:
    ticker: str
    sleeve: str
    source: str                      # "bora" | "screener"
    price: float
    components: list[Component] = field(default_factory=list)
    unknown: list[str] = field(default_factory=list)

    def add(self, name: str, points: float, reason: str,
            blocking: bool = False) -> None:
        self.components.append(Component(name, points, reason, blocking))

    @property
    def blockers(self) -> list[Component]:
        return [c for c in self.components if c.blocking]

    @property
    def takeable(self) -> bool:
        return not self.blockers

    @property
    def score(self) -> float:
        base = sum(c.points for c in self.components)
        return round(base + (BLOCKED_PENALTY if self.blockers else 0.0), 1)


# --------------------------------------------------------------------------
# Components
# --------------------------------------------------------------------------

def _score_drift(cand: dict, sleeve: Sleeve, policy: Policy,
                 out: Ranked) -> None:
    """How far past his cost the price has run, against the sleeve's limit."""
    cost = cand.get("his_avg_cost")
    price = cand.get("price")
    if not cost or not price:
        out.unknown.append("drift (no cost basis)")
        return

    drift = (price - cost) / cost * 100.0
    limit = sleeve.holdings_drift_pct

    trend = str(cand.get("trend", "")).lower()
    falling = "downtrend" in trend
    tag = _normalize_tag(cand.get("porttech", ""))

    if drift > limit:
        out.add("drift", 0.0,
                f"{drift:+.1f}% above his cost, over the {limit:.0f}% "
                f"{sleeve.code} limit", blocking=True)
    elif drift <= 0:
        # A falling knife is not a discount.
        #
        # "Below his cost" was scored as the cleanest possible entry, which is
        # backwards when the reason it is cheap is that it is still falling.
        # Live case that exposed this: a name 22.9% under his last buy, in a
        # downtrend, tagged Kritik — it ranked FIRST at 90/100. Being cheap
        # relative to someone else's entry says nothing about why.
        deep = drift < -10.0
        if drift < -20.0:
            # Magnitude alone is enough. AAOI reached -30% carrying an İzle tag
            # rather than Kritik, so a condition requiring Kritik-or-downtrend
            # never fired and it read as "inside the limit".
            out.add("drift", 0.0,
                    f"{drift:+.1f}% below his cost — he is deeply underwater, "
                    f"whatever the tag says", blocking=True)
        elif deep and (falling or tag == "Kritik"):
            why = "in a downtrend" if falling else "flagged Kritik by his own system"
            out.add("drift", 0.0,
                    f"{drift:+.1f}% below his cost and {why} — he is underwater "
                    f"and it has not stopped", blocking=True)
        elif deep:
            out.add("drift", 5.0,
                    f"{drift:+.1f}% below his cost — a big gap. Cheaper than he "
                    f"paid is not the same as cheap; find out why before reading "
                    f"it as an opportunity")
        elif falling:
            out.add("drift", 5.0,
                    f"{drift:+.1f}% below his cost but trending down — near his "
                    f"entry, though momentum is against you")
        else:
            out.add("drift", 30.0,
                    f"{drift:+.1f}% — at or below his cost with the trend intact, "
                    f"the cleanest entry")
    elif drift <= limit * 0.25:
        out.add("drift", 25.0, f"{drift:+.1f}% above his cost, well inside {limit:.0f}%")
    elif drift <= limit * 0.5:
        out.add("drift", 15.0, f"{drift:+.1f}% above his cost")
    else:
        out.add("drift", 5.0,
                f"{drift:+.1f}% above his cost, approaching the {limit:.0f}% limit")


def _score_direction(cand: dict, out: Ranked, recency_days: int = 45) -> None:
    """His own most recent action in the name. Buying what he is selling is
    the single most important thing to surface, so it weighs heavily."""
    last = cand.get("his_last_action") or {}
    action = str(last.get("action", "")).lower()
    action = {"alım": "buy", "alim": "buy", "satış": "sell",
              "satis": "sell"}.get(action, action)
    when = last.get("date")
    age = _age_days(when)

    if not action or age is None:
        out.unknown.append("his last action")
        return

    if action == "sell" and age <= recency_days:
        out.add("his_direction", -25.0,
                f"he SOLD this {age}d ago — you would be buying what he is reducing")
    elif action == "sell":
        out.add("his_direction", -5.0, f"his last action was a sell, {age}d ago (stale)")
    elif action == "buy" and age <= recency_days:
        out.add("his_direction", 20.0, f"he BOUGHT this {age}d ago")
    else:
        out.add("his_direction", 5.0, f"his last action was a buy, {age}d ago")


def _score_porttech(cand: dict, out: Ranked) -> None:
    """His own health tag. Him flagging his own position is evidence."""
    tag = _normalize_tag(cand.get("porttech", ""))
    if tag == "Sağlıklı":
        out.add("porttech", 10.0, "his tag: Sağlıklı (healthy)")
    elif tag == "İzle":
        out.add("porttech", -5.0, "his tag: İzle (watch)")
    elif tag == "Kritik":
        out.add("porttech", -20.0, "his tag: Kritik — his own system flags this")
    else:
        out.unknown.append("his Porttech tag")


def _score_manageability(cand: dict, sleeve: Sleeve, policy: Policy,
                         out: Ranked) -> None:
    """Can the position be trimmed or scaled after entry, or only opened and
    closed whole? A 1-share position is all-or-nothing."""
    price = cand.get("price")
    if not price:
        return
    ceiling = policy.position_ceiling(sleeve)
    shares = int(ceiling // price)

    if shares == 0:
        frac = cand.get("fractional")
        if frac:
            out.add("manageability", -10.0,
                    f"1 share costs {price:,.2f}, over the {ceiling:,.0f} "
                    f"ceiling — fractional only")
        else:
            out.add("manageability", 0.0,
                    f"1 share costs {price:,.2f}, over the {ceiling:,.0f} "
                    f"ceiling and no fractional", blocking=True)
    elif shares < 3:
        out.add("manageability", -15.0,
                f"ceiling buys only {shares} whole share(s) — cannot trim or scale")
    else:
        out.add("manageability", 10.0, f"ceiling buys {shares} shares — manageable")


def _score_liquidity(cand: dict, policy: Policy, out: Ranked) -> None:
    volume = cand.get("avg_volume")
    if volume is None:
        out.unknown.append("average volume")
    elif volume < policy.min_avg_volume:
        out.add("liquidity", 0.0,
                f"{volume:,.0f}/day is below the {policy.min_avg_volume:,.0f} "
                f"floor", blocking=True)
    else:
        out.add("liquidity", 5.0, f"{volume:,.0f}/day")

    spread = cand.get("spread_pct")
    if spread is not None and spread > policy.max_spread_pct:
        out.add("spread", -10.0, f"spread {spread:.2f}% of mid is wide")


def _score_earnings(cand: dict, policy: Policy, out: Ranked,
                    today: dt.date | None = None) -> None:
    today = today or dt.date.today()
    when = _as_date(cand.get("next_earnings"))
    if when is None:
        out.unknown.append("next earnings date")
        return
    days = (when - today).days
    if 0 <= days <= policy.earnings_blackout_days:
        out.add("earnings", 0.0,
                f"earnings in {days}d — inside the blackout", blocking=True)
    elif 0 <= days <= 10:
        out.add("earnings", -10.0, f"earnings in {days}d")
    else:
        out.add("earnings", 5.0, f"earnings {days}d out")


def _score_headroom(cand: dict, sleeve: Sleeve, policy: Policy, account: dict,
                    out: Ranked) -> None:
    positions = [p for p in (account.get("positions") or [])
                 if str(p.get("sleeve", "")).upper() == sleeve.code]
    used = sum(float(p.get("market_value", 0.0)) for p in positions)
    budget = sleeve.budget(policy.risk_base)
    remaining = budget - used
    ceiling = policy.position_ceiling(sleeve)

    if remaining <= 0:
        out.add("headroom", 0.0,
                f"{sleeve.code} sleeve fully allocated", blocking=True)
    elif remaining >= ceiling:
        out.add("headroom", 10.0,
                f"{sleeve.code} has {remaining:,.0f} left, enough for a full position")
    else:
        out.add("headroom", 0.0,
                f"{sleeve.code} has only {remaining:,.0f} left — partial size only")

    if len(positions) >= sleeve.max_positions:
        out.add("sleeve_count", 0.0,
                f"{sleeve.code} already holds {len(positions)} positions",
                blocking=True)


def _score_concentration(cand: dict, policy: Policy, account: dict,
                         out: Ranked) -> None:
    sector = cand.get("sector")
    if not sector:
        out.unknown.append("sector")
        return
    held = sum(float(p.get("market_value", 0.0))
               for p in (account.get("positions") or [])
               if str(p.get("sector", "")).lower() == sector.lower())
    pct = held / policy.risk_base * 100.0 if policy.risk_base else 0.0
    if pct > policy.max_sector_pct:
        out.add("concentration", -15.0,
                f"{sector} already {pct:.0f}% of the risk base")
    else:
        out.add("concentration", 0.0, f"{sector} at {pct:.0f}% of the risk base")


def _score_trend(cand: dict, out: Ranked) -> None:
    trend = str(cand.get("trend", "")).lower()
    if "downtrend" in trend:
        out.add("trend", -10.0, "our read: downtrend")
    elif "uptrend" in trend or "strong" in trend:
        out.add("trend", 10.0, f"our read: {trend}")
    elif trend:
        out.add("trend", 0.0, f"our read: {trend}")
    else:
        out.unknown.append("trend")


# --------------------------------------------------------------------------
# Pipeline
# --------------------------------------------------------------------------

def rank_one(cand: dict, policy: Policy, account: dict,
             today: dt.date | None = None) -> Ranked:
    sleeve = policy.sleeve(cand.get("sleeve") or "P1")
    out = Ranked(
        ticker=str(cand.get("ticker", "")).upper(),
        sleeve=sleeve.code,
        source=str(cand.get("source", "bora")).lower(),
        price=float(cand.get("price") or 0.0),
    )

    if not sleeve.tradeable:
        out.add("sleeve_untradeable", 0.0,
                f"{sleeve.code} cannot be traded in this account", blocking=True)
        return out

    _score_drift(cand, sleeve, policy, out)
    _score_direction(cand, out)
    _score_porttech(cand, out)
    _score_manageability(cand, sleeve, policy, out)
    _score_liquidity(cand, policy, out)
    _score_earnings(cand, policy, out, today)
    _score_headroom(cand, sleeve, policy, account, out)
    _score_concentration(cand, policy, account, out)
    _score_trend(cand, out)
    return out


def rank_all(candidates: list[dict], policy: Policy, account: dict,
             today: dt.date | None = None) -> list[Ranked]:
    ranked = [rank_one(c, policy, account, today) for c in candidates]
    # Deterministic: score desc, then ticker asc so equal scores never shuffle
    # between runs and a diff of two days' lists means something.
    ranked.sort(key=lambda r: (-r.score, r.ticker))
    return ranked


def render(ranked: list[Ranked], title: str, note: str = "") -> str:
    lines: list[str] = []
    add = lines.append
    add("=" * 72)
    add(f"  {title}")
    add("=" * 72)
    if note:
        add(f"  {note}")
        add("")

    takeable = [r for r in ranked if r.takeable]
    blocked = [r for r in ranked if not r.takeable]

    if takeable:
        add(f"TAKEABLE ({len(takeable)})")
        add("-" * 72)
        for r in takeable:
            add(f"  {r.score:>6.1f}  {r.ticker:<6} {r.sleeve}  @ {r.price:,.2f}")
            for c in sorted(r.components, key=lambda c: -c.points):
                if c.points:
                    add(f"          {c.points:+6.1f}  {c.name}: {c.reason}")
            if r.unknown:
                add(f"          unknown: {', '.join(r.unknown)}")
            add("")

    if blocked:
        add(f"NOT TAKEABLE ({len(blocked)}) — listed so you know they exist")
        add("-" * 72)
        for r in blocked:
            reasons = "; ".join(c.reason for c in r.blockers)
            add(f"  {r.ticker:<6} {r.sleeve}  @ {r.price:,.2f}  — {reasons}")
        add("")

    add("-" * 72)
    add("This ranks TAKEABILITY — whether a clean entry is available today at")
    add("your size under your rules. It is not a return forecast and a high")
    add("score is not a recommendation.")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _normalize_tag(raw: str) -> str | None:
    lowered = (raw or "").lower()
    for key, tag in (("kritik", "Kritik"), ("kırılgan", "Kritik"),
                     ("kirilgan", "Kritik"), ("izle", "İzle"),
                     ("sağlıklı", "Sağlıklı"), ("saglikli", "Sağlıklı")):
        if key in lowered:
            return tag
    return None


def _as_date(value):
    if not value:
        return None
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _age_days(value) -> int | None:
    when = _as_date(value)
    return (dt.date.today() - when).days if when else None


def _load(path: str | None) -> dict | list:
    if not path:
        return {}
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Rank candidates by takeability (not by expected return).")
    parser.add_argument("--candidates", required=True)
    parser.add_argument("--account")
    parser.add_argument("--source", choices=["bora", "screener", "all"],
                        default="all")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    profile = _load_risk_profile()
    policy = Policy()
    if profile.get("risk_base"):
        policy.risk_base = float(profile["risk_base"])
    sleeves = _sleeves_from_profile(profile)
    if sleeves:
        policy.sleeves = sleeves

    try:
        candidates = _load(args.candidates)
        account = _load(args.account) if args.account else {}
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    if args.source != "all":
        candidates = [c for c in candidates
                      if str(c.get("source", "bora")).lower() == args.source]

    ranked = rank_all(candidates, policy, account)

    if args.json:
        print(json.dumps([{
            "ticker": r.ticker, "sleeve": r.sleeve, "source": r.source,
            "price": r.price, "score": r.score, "takeable": r.takeable,
            "components": [{"name": c.name, "points": c.points,
                            "reason": c.reason, "blocking": c.blocking}
                           for c in r.components],
            "unknown": r.unknown,
        } for r in ranked], indent=2))
        return 0

    bora = [r for r in ranked if r.source == "bora"]
    screener = [r for r in ranked if r.source == "screener"]
    if bora:
        print(render(bora, "FROM BORA — his holdings and recent buys"))
    if screener:
        if bora:
            print()
        print(render(screener, "SCREENER — NO THESIS FROM HIM",
                     "These matched a filter, not his conviction. Taking one is "
                     "independent stock-picking, a different activity."))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
