"""Tests for rank.py.

Expectations are hand-computed from the component table in rank.py, because a
ranking you cannot verify by hand is a ranking you should not trade on.

Run:  .venv/bin/python tools/test_rank.py
"""

from __future__ import annotations

import datetime as dt
import sys

import rank
import verify

FAILURES: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label} {detail}")
        FAILURES.append(label)


TODAY = dt.date(2026, 8, 15)


def policy() -> verify.Policy:
    return verify.Policy(risk_base=20_000.0)


def account(**overrides) -> dict:
    acct = {"account_value": 3_000.0, "buying_power": 3_000.0,
            "settled_cash": 3_000.0, "positions": []}
    acct.update(overrides)
    return acct


def candidate(**overrides) -> dict:
    """A clean, unremarkable P1 name: at his cost, he bought recently, healthy
    tag, cheap enough to scale, liquid, no earnings soon, uptrend."""
    cand = {
        "ticker": "TEST", "sleeve": "P1", "source": "bora",
        "his_avg_cost": 100.0, "price": 100.0,
        "porttech": "Sağlıklı (score 70)",
        "his_last_action": {"action": "buy",
                            "date": (TODAY - dt.timedelta(days=5)).isoformat()},
        "avg_volume": 5_000_000, "spread_pct": 0.02,
        "next_earnings": (TODAY + dt.timedelta(days=45)).isoformat(),
        "sector": "Technology", "trend": "uptrend", "fractional": True,
    }
    cand.update(overrides)
    return cand


def points(ranked, name):
    for c in ranked.components:
        if c.name == name:
            return c.points
    return None


def run(cand=None, acct=None):
    return rank.rank_one(cand or candidate(), policy(), acct or account(), TODAY)


# --------------------------------------------------------------------------

def test_clean_candidate_components():
    """Hand-computed: drift +30 (at cost), direction +20 (bought 5d ago),
    porttech +10, manageability +10 (1000/100 = 10 shares), liquidity +5,
    earnings +5 (45d out), headroom +10, concentration 0, trend +10 = 100."""
    print("Clean candidate")
    r = run()
    check("takeable", r.takeable, f"blockers={[c.name for c in r.blockers]}")
    check("drift +30 at his cost", points(r, "drift") == 30.0)
    check("direction +20 recent buy", points(r, "his_direction") == 20.0)
    check("porttech +10", points(r, "porttech") == 10.0)
    check("manageability +10", points(r, "manageability") == 10.0)
    check("earnings +5", points(r, "earnings") == 5.0)
    check("headroom +10", points(r, "headroom") == 10.0)
    check("trend +10", points(r, "trend") == 10.0)
    check("total 100", r.score == 100.0, f"got {r.score}")


def test_recent_sell_ranks_below_recent_buy():
    """The MU lesson, in the ranker: identical names differing only in his
    direction must not sort together."""
    print("His direction dominates")
    buyer = run()
    seller = run(cand=candidate(his_last_action={
        "action": "Satış", "date": (TODAY - dt.timedelta(days=5)).isoformat()}))
    check("sell scores -25", points(seller, "his_direction") == -25.0)
    check("seller ranks below buyer", seller.score < buyer.score)
    check("45-point gap", buyer.score - seller.score == 45.0,
          f"got {buyer.score - seller.score}")

    stale = run(cand=candidate(his_last_action={
        "action": "sell", "date": (TODAY - dt.timedelta(days=200)).isoformat()}))
    check("stale sell is only -5", points(stale, "his_direction") == -5.0)


def test_drift_blocks_over_sleeve_limit():
    print("Drift vs sleeve limit")
    # P1 limit is 20%. 125 vs cost 100 = +25% -> blocking.
    over = run(cand=candidate(price=125.0))
    check("over limit blocks", not over.takeable)
    check("blocker is drift", "drift" in [c.name for c in over.blockers])

    # P4 limit is 5%. Same +10% passes in P1, blocks in P4.
    p1 = run(cand=candidate(price=110.0, sleeve="P1"))
    p4 = run(cand=candidate(price=110.0, sleeve="P4"))
    check("+10% takeable in P1", p1.takeable)
    check("+10% blocked in P4", not p4.takeable)

    below = run(cand=candidate(price=95.0))
    check("below his cost still scores the max +30",
          points(below, "drift") == 30.0)


def test_falling_knife_is_not_a_discount():
    """Regression from a live run: a name 22.9% under his last buy, in a
    downtrend, tagged Kritik, ranked FIRST at 90/100 because "below his cost"
    scored as the cleanest possible entry. Being cheap relative to someone
    else's entry says nothing about why it is cheap."""
    print("Falling knife")
    knife = run(cand=candidate(price=77.1, his_avg_cost=100.0,
                               trend="downtrend", porttech="Kritik (2, score 5)"))
    check("deep discount + downtrend blocks", not knife.takeable,
          f"blocks={[c.name for c in knife.blockers]}")
    check("the blocker is drift, not something incidental",
          "drift" in [c.name for c in knife.blockers])
    blocker = next(c for c in knife.blockers if c.name == "drift")
    check("says he is underwater and it has not stopped",
          "underwater" in blocker.reason)

    # Kritik alone is enough — the tag is his own system disagreeing with him.
    tagged = run(cand=candidate(price=77.1, his_avg_cost=100.0,
                                trend="uptrend", porttech="Kritik"))
    check("deep discount + Kritik blocks even without a downtrend",
          not tagged.takeable)

    # A shallow dip below his cost with the trend intact is still the best case.
    clean = run(cand=candidate(price=98.0, his_avg_cost=100.0))
    check("shallow dip in an uptrend still scores +30",
          points(clean, "drift") == 30.0, f"got {points(clean, 'drift')}")

    # -15%: deep enough to demote, not deep enough to block on magnitude.
    lonely = run(cand=candidate(price=85.0, his_avg_cost=100.0,
                                trend="mixed / range-bound",
                                porttech="Sağlıklı"))
    check("-15% alone demotes to +5, not blocked",
          lonely.takeable and points(lonely, "drift") == 5.0,
          f"got {points(lonely, 'drift')}")

    # Beyond -20%, magnitude alone blocks regardless of tag or trend. AAOI
    # reached -30% on an İzle tag, so a rule needing Kritik-or-downtrend never
    # fired and it read as "inside the limit".
    izle = run(cand=candidate(price=69.7, his_avg_cost=100.0,
                              trend="mixed / range-bound",
                              porttech="İzle (1, score 45)"))
    check("-30% blocks on magnitude even with only an İzle tag",
          not izle.takeable, f"blocks={[c.name for c in izle.blockers]}")

    # And the headline property: a knife must not outrank a healthy name.
    healthy = candidate(ticker="GOOD")
    ranked = rank.rank_all([candidate(ticker="KNIFE", price=77.1,
                                      his_avg_cost=100.0, trend="downtrend",
                                      porttech="Kritik"), healthy],
                           policy(), account(), TODAY)
    check("healthy name outranks the knife", ranked[0].ticker == "GOOD")


def test_manageability_demotes():
    print("Manageability")
    # P1 ceiling is 1000. At 400/share that is 2 whole shares.
    two = run(cand=candidate(price=400.0, his_avg_cost=400.0))
    check("2 shares scores -15", points(two, "manageability") == -15.0)
    check("still takeable", two.takeable)

    # At 333/share -> exactly 3 shares -> the positive branch
    three = run(cand=candidate(price=333.0, his_avg_cost=333.0))
    check("3 shares scores +10", points(three, "manageability") == 10.0)

    # Above the ceiling with no fractional -> blocking
    blocked = run(cand=candidate(price=1500.0, his_avg_cost=1500.0,
                                 fractional=False))
    check("over ceiling without fractional blocks", not blocked.takeable)

    frac = run(cand=candidate(price=1500.0, his_avg_cost=1500.0,
                              fractional=True))
    check("over ceiling with fractional is a penalty, not a block",
          frac.takeable and points(frac, "manageability") == -10.0)


def test_hard_blocks():
    print("Hard blocks")
    thin = run(cand=candidate(avg_volume=1_000))
    check("thin volume blocks", not thin.takeable)

    earnings = run(cand=candidate(
        next_earnings=(TODAY + dt.timedelta(days=2)).isoformat()))
    check("earnings blackout blocks", not earnings.takeable)

    p5 = run(cand=candidate(sleeve="P5"))
    check("P5 blocks as untradeable", not p5.takeable)
    check("P5 blocker named", "sleeve_untradeable" in
          [c.name for c in p5.blockers])

    full = [{"ticker": f"T{i}", "market_value": 1_100.0, "sleeve": "P1",
             "sector": "Technology"} for i in range(14)]
    no_room = run(acct=account(positions=full))
    check("full sleeve blocks", not no_room.takeable)


def test_blocked_never_outranks_clean():
    """A blocked name may carry many positive components; it must still sort
    below every takeable name."""
    print("Blocked ordering")
    clean_low = candidate(ticker="LOW", porttech="Kritik", trend="downtrend",
                          his_last_action={"action": "sell",
                                           "date": (TODAY - dt.timedelta(days=5)).isoformat()})
    blocked_high = candidate(ticker="HIGH", avg_volume=100)
    ranked = rank.rank_all([blocked_high, clean_low], policy(), account(), TODAY)
    check("takeable name sorts first", ranked[0].ticker == "LOW")
    check("blocked name last", ranked[-1].ticker == "HIGH")
    check("blocked score is heavily penalised", ranked[-1].score < -900)


def test_ordering_is_deterministic():
    print("Deterministic ordering")
    a = candidate(ticker="BBB")
    b = candidate(ticker="AAA")
    first = [r.ticker for r in rank.rank_all([a, b], policy(), account(), TODAY)]
    second = [r.ticker for r in rank.rank_all([b, a], policy(), account(), TODAY)]
    check("equal scores break ties by ticker", first == ["AAA", "BBB"])
    check("input order does not matter", first == second)


def test_unknowns_are_reported_not_assumed():
    print("Unknowns")
    sparse = {"ticker": "X", "sleeve": "P1", "source": "bora", "price": 50.0}
    r = rank.rank_one(sparse, policy(), account(), TODAY)
    check("missing cost basis reported",
          any("drift" in u for u in r.unknown))
    check("missing tag reported", any("Porttech" in u for u in r.unknown))
    check("missing earnings reported", any("earnings" in u for u in r.unknown))
    check("missing volume reported", any("volume" in u for u in r.unknown))
    check("not silently blocked for missing data", r.takeable)


def test_render_separates_and_disclaims():
    print("Rendering")
    ranked = rank.rank_all([candidate(), candidate(ticker="BAD", avg_volume=1)],
                           policy(), account(), TODAY)
    text = rank.render(ranked, "TEST LIST")
    check("splits takeable from blocked",
          "TAKEABLE (1)" in text and "NOT TAKEABLE (1)" in text)
    check("shows component breakdown", "drift:" in text)
    check("disclaims forecasting", "not a return forecast" in text)


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S): {', '.join(FAILURES)}")
        return 1
    print(f"All {len(tests)} rank test groups passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
