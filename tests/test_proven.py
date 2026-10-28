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


def test_a_proven_over_posts_when_the_road_team_is_at_altitude(monkeypatch, tmp_path):
    """Owner, Oct 28 (more proven spots from our own data): college hoops over, road team at altitude."""
    import json
    from datetime import datetime, timedelta, timezone

    from sportsedge import factorstudy

    row = {"market": "over", "variant": "road team visiting altitude", "verdict": "STRONG", "study": "College hoops game factors",
           "blind": {"bets": 549, "flat_roi": 0.154, "se": 0.04}, "in_sample": {"bets": 494, "flat_roi": 0.071}}
    (tmp_path / "factor_study.json").write_text(json.dumps({"ncaab": {"rows": [row]}}))
    monkeypatch.setattr(factorstudy, "game_study", lambda *a, **k: {"Kansas Jayhawks @ Colorado Buffaloes": [
        {"team": "Kansas Jayhawks", "side": "away", "factor": "visiting altitude", "markets": [], "best": "untested"}]})
    now = datetime(2026, 11, 10, 15, tzinfo=timezone.utc)
    over = make_pick(market="totals", side="over", point=141.5, american=-110, sport="ncaab", event_id="g1")
    over = __import__("dataclasses").replace(over, commence_time=now + timedelta(hours=8))
    ctx = SimpleNamespace(home="Colorado Buffaloes", away="Kansas Jayhawks", neutral=False, injuries_unknown=[], wind_mph=None,
                          event=SimpleNamespace(commence_time=now + timedelta(hours=8)))
    scan = SimpleNamespace(sport=SimpleNamespace(key="ncaab", name="College Basketball"), picks=[over], contexts={"g1": ctx})
    got = proven.find([scan], now, tmp_path)
    assert [(c.pick.side, c.units, c.proven.split(":")[0]) for c in got] == [("over", 1.0, "STRONG")]
