"""Owner, Oct 25: only bets that passed a blind test get money."""

from types import SimpleNamespace

from sportsedge import proven
from sportsedge.d5.board import PROVEN_TIERS
from sportsedge.tiers import NO_PROOF, Card, gate
from tests.test_ledger import make_pick


def card(tier="lock", units=1.0, proven_tag="", best=1):
    p = make_pick(market="h2h", point=None, american=-140)
    return Card(tier=tier, units=units, headline="h", reasons=["r"], sport="nfl", sport_name="NFL", pick=p,
                best_rank=best, proven=proven_tag)


def test_an_unproven_lock_posts_as_a_no_unit_lean_and_leaves_the_top_bets():
    c = gate(card())
    assert (c.tier, c.units, c.best_rank) == ("lean", 0.0, None) and c.reasons[0] == NO_PROOF
    v = gate(card(tier="value"))
    assert (v.tier, v.units) == ("value", 0.0)
    kept = gate(card(proven_tag="STRONG: test"))
    assert (kept.tier, kept.units, kept.best_rank) == ("lock", 1.0, 1)


def test_proven_cards_price_their_tested_edge_and_respect_the_150_cap():
    row = {"verdict": "STRONG", "variant": "back: off a blowout win (20+)", "study": "NBA game factors",
           "blind": {"bets": 194, "flat_roi": 0.0935, "se": 0.0615}, "in_sample": {"bets": 164, "flat_roi": 0.086}}
    ctx = SimpleNamespace(home="Wolves", away="Raptors")
    pick = make_pick(market="h2h", point=None, american=-115)
    c = proven._card("NBA", ctx, pick, row, "Wolves off a blowout win (20+)")
    assert c.units == 1.0 and c.proven.startswith("STRONG")
    assert abs(c.pick.win_prob - (1 + 0.032) / (1 + 100 / 115)) < 1e-3  # +9.35% less one standard error
    assert proven._fits("moneyline favorite", make_pick(market="h2h", point=None, american=-150))
    assert not proven._fits("moneyline favorite", make_pick(market="h2h", point=None, american=-160))
    assert not proven._fits("moneyline dog", make_pick(market="h2h", point=None, american=320))


def test_only_spots_that_passed_are_looked_for(tmp_path):
    import json

    (tmp_path / "factor_study.json").write_text(json.dumps({"nba": {"rows": [
        {"market": "spread", "variant": "fade: this team struggles on short rest (its own history)", "verdict": "PROVEN"},
        {"market": "moneyline favorite", "variant": "back: off a blowout win (20+)", "verdict": "STRONG"},
        {"market": "spread", "variant": "back: on a back-to-back", "verdict": ""}]}}))
    (tmp_path / "line_studies.json").write_text(json.dumps({"rows": [
        {"study": "06 weather totals", "variant": "under, wind 15+ mph", "sport": "nfl", "verdict": "STRONG"},
        {"study": "07 altitude", "variant": "home dog at altitude", "sport": "nfl", "verdict": "PROVEN"}]}))
    got = {(r["sport"], r["variant"]) for r in proven.passed(tmp_path)}
    assert got == {("nba", "back: off a blowout win (20+)"), ("nfl", "under, wind 15+ mph")}


def test_only_his_nhl_lock_and_early_football_dogs_carry_units():
    assert set(PROVEN_TIERS) == {("nhl", "lock"), ("nfl", "early"), ("ncaaf", "early")}
