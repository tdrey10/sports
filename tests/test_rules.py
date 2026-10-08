"""The blueprint's money rules: each one gets a test so it never quietly comes back."""

from dataclasses import replace

from sportsedge.data.espn import parse_injuries
from sportsedge.data.sample import SampleProvider
from sportsedge.edge import Settings, qualifying
from sportsedge.engine import Engine, injury_factor
from sportsedge.models.game import predict_game
from sportsedge.sports import SPORTS, resolve
from sportsedge.tiers import build_cards

from tests.test_ledger import make_pick


def test_never_bet_worse_than_minus_150():
    s = Settings(min_ev=0.0)
    ok = make_pick(american=-150, ev=0.03)
    too_short = make_pick(american=-151, ev=0.03, event_id="ev2")
    assert qualifying([ok, too_short], s) == [ok]


def test_injury_report_lists_every_team_with_positions():
    report = parse_injuries({"injuries": [
        {"displayName": "Buffalo Bills", "injuries": [
            {"athlete": {"displayName": "Josh Allen", "position": {"abbreviation": "QB"}}, "status": "Out"}]},
        {"displayName": "Miami Dolphins", "injuries": []},
    ]})
    assert report["Buffalo Bills"] == [("Josh Allen", "Out", "QB")]
    assert report["Miami Dolphins"] == []  # listed with nobody out: known healthy, not unknown


def test_nfl_qb_out_costs_about_eight_points_of_win_chance():
    from sportsedge.models.ratings import TeamRatings

    cfg = SPORTS["nfl"]
    r = TeamRatings(link="identity", mu=22.0, hfa=0.0, off={"A": 0.0, "B": 0.0}, deff={"A": 0.0, "B": 0.0})
    base = predict_game(cfg, r, "A", "B", neutral=True).p_home
    eh, _ = r.expected_scores("A", "B", True)
    hurt = predict_game(cfg, r, "A", "B", neutral=True, home_factor=injury_factor(cfg, 0.08, eh)).p_home
    assert 0.06 < base - hurt < 0.10
    assert injury_factor(SPORTS["nhl"], 0.08, 3.0) == 1.0  # weights only where the blueprint sets them


def test_unknown_injuries_mean_no_bet():
    provider = SampleProvider(seed=5)
    engine = Engine(provider, Settings(), now=provider.now)
    scans = [engine.scan(c) for c in resolve("nba,epl")]
    epl = next(s for s in scans if s.sport.key == "epl")
    assert epl.contexts and all(ctx.injuries_unknown for ctx in epl.contexts.values())  # no soccer injury feed
    nba = next(s for s in scans if s.sport.key == "nba")
    assert nba.contexts and not any(ctx.injuries_unknown for ctx in nba.contexts.values())
    cards = build_cards(scans, engine.settings, provider.now)
    soccer = [c for c in cards if c.sport == "epl"]
    assert all(c.tier == "lean" and c.units == 0 for c in soccer)
    assert all(c.reasons[0].startswith("No bet: no injury report") for c in soccer)
    assert any(c.units > 0 for c in cards if c.sport == "nba")


def test_failed_injury_feed_is_unknown_not_healthy():
    provider = SampleProvider(seed=5)

    def broken(cfg):
        raise RuntimeError("feed down")

    provider.injuries = broken
    engine = Engine(provider, Settings(), now=provider.now)
    scan = engine.scan(SPORTS["nba"])
    assert scan.contexts and all(ctx.injuries_unknown for ctx in scan.contexts.values())
    assert not [c for c in build_cards([scan], engine.settings, provider.now) if c.units > 0]


def test_unproven_bet_types_are_sized_at_half_a_unit():
    from sportsedge.tiers import units_for

    big = make_pick(kelly=0.03, sport="nfl", market="spreads")
    assert units_for(big) == 3.0  # no studies loaded: normal quarter-Kelly sizing
    assert units_for(big, Settings(proven=set())) == 0.5
    assert units_for(big, Settings(proven={"nfl:spreads"})) == 3.0
    assert units_for(big, Settings(proven={"nfl:over/under", "nba:spreads"})) == 0.5
