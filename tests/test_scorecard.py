"""Owner, Oct 28 (+EV spec): every pick carries the required scorecard and an evidence-based grade."""

from datetime import datetime, timezone

from sportsedge.scorecard import build
from sportsedge.tiers import Card, gate
from tests.test_ledger import make_pick

NOW = datetime(2026, 10, 28, 15, tzinfo=timezone.utc)


def card(units=1.0, american=-115, win=0.552, market=0.53, evidence=None, proven="x", reasons=("why",)):
    p = make_pick(market="h2h", point=None, american=american, win_prob=win, market_prob=market)
    return Card(tier="value", units=units, headline="h", reasons=list(reasons), sport="nba", sport_name="NBA", pick=p,
                proven=proven if units > 0 else "", evidence=evidence)


STRONG = {"kind": "STRONG", "blind_bets": 194, "blind_roi": 0.0935, "blind_se": 0.0615, "text": "NBA favorite off a 20+ win"}


def test_a_strong_spot_is_a_plus_with_every_field_filled():
    s = build(card(evidence=STRONG), NOW)
    assert s["grade"] == "A+"
    for k in ("book", "odds", "model_prob", "implied_prob", "fair_market_prob", "fair_odds", "edge_pts", "ev_pct",
              "range", "min_odds", "validation", "factors", "risks", "why", "checked_at"):
        assert s[k] is not None, k
    assert s["range"][0] <= 0.552 < s["range"][1]  # proven picks are priced at the low end, on purpose and s["edge_pts"] == 2.2 and s["min_odds"] < -115


def test_grades_follow_the_evidence_not_the_models_confidence():
    proven = dict(STRONG, kind="PROVEN")
    assert build(card(evidence=proven), NOW)["grade"] == "A"
    clv = {"kind": "CLV", "blind_bets": 200, "blind_roi": 0.062, "blind_se": 0.106, "why": "beats the close"}
    sc = build(card(american=310, win=0.26, market=0.24, evidence=clv), NOW)
    assert sc["grade"] == "B" and any("standard error" in r for r in sc["risks"])
    lean = gate(card(proven="", win=0.60, market=0.53))  # the model loves it, nothing validated it
    assert (lean.units, lean.grade) == (0.0, "C") and lean.scorecard["validation"].startswith("None")
    assert gate(card(proven="", win=0.50, market=0.53)).grade == "NO BET"


def test_a_strong_spot_too_thin_at_this_price_drops_to_a():
    thin = dict(STRONG, blind_roi=0.03, blind_se=0.05)  # low end of the range has no edge
    s = build(card(evidence=thin, win=1.03 / (1 + 100 / 115)), NOW)
    assert s["grade"] == "A" and any("Sensitive" in r for r in s["risks"])
