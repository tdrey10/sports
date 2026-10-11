from datetime import timedelta

from sportsedge import forecasts
from sportsedge.data.sample import SampleProvider
from sportsedge.edge import Settings
from sportsedge.engine import Engine
from sportsedge.ledger import Ledger
from sportsedge.models.ratings import GameResult
from sportsedge.sports import resolve


def _scans():
    provider = SampleProvider(seed=5)
    engine = Engine(provider, Settings(), now=provider.now)
    return provider, [engine.scan(c) for c in resolve("nba,nhl,epl")]


def test_every_game_gets_a_forecast_whose_score_names_the_same_winner():
    provider, scans = _scans()
    fs = forecasts.build(scans, Settings(), provider.now)
    games = sum(len(s.predictions) for s in scans)
    assert fs and len(fs) == games  # every game, not just picks
    for f in fs:
        total = f.p_home + f.p_away + f.p_draw
        assert abs(total - 1) < 1e-3
        assert f.winner == max(("home", "away", "draw"), key=lambda s: {"home": f.p_home, "away": f.p_away, "draw": f.p_draw}[s])
        h, a = forecasts.whole_score(f)
        if f.winner == "home":
            assert h > a
        elif f.winner == "away":
            assert a > h
        else:
            assert h == a
    assert any(f.p_draw > 0 for f in fs if f.sport == "epl") and all(f.p_draw == 0 for f in fs if f.sport == "nba")


def test_blend_leans_on_the_market_at_the_learned_weight():
    provider, scans = _scans()
    all_market = forecasts.build(scans, Settings(market_weight=1.0), provider.now)
    priced = [f for f in all_market if f.market_home is not None and f.sport != "epl"]
    assert priced and all(abs(f.p_home - f.market_home) < 1e-3 for f in priced)
    model_only = forecasts.build(scans, Settings(market_weight=0.0), provider.now)
    assert all(abs(f.p_home - f.model_home) < 1e-3 for f in model_only if f.sport != "epl")


def test_forecasts_freeze_at_kickoff_grade_on_the_final_and_survive_save(tmp_path):
    provider, scans = _scans()
    now = provider.now
    stored = {}
    f = forecasts.build(scans, Settings(), now)[0]
    forecasts.merge(stored, [f], now)
    later = f.start + timedelta(minutes=5)
    changed = forecasts.Forecast.from_dict({**f.to_dict(), "home_score": 999.0})
    forecasts.merge(stored, [changed], later)
    assert stored[f.id].home_score == f.home_score  # frozen once the game starts
    win_home = f.winner == "home"
    game = GameResult("x", f.start, f.home, f.away, 30 if win_home else 10, 10 if win_home else 30, state="post")
    assert forecasts.grade(stored, lambda sport, s, e: [game], later + timedelta(hours=4)) == 1
    g = stored[f.id]
    assert g.status == "final" and g.correct is (f.winner in ("home", "away"))
    rec = forecasts.record(stored)
    assert rec["overall"]["right"] + rec["overall"]["wrong"] == 1 and rec["overall"]["score_error"] is not None
    L = Ledger()
    L.forecasts = stored
    L.save(tmp_path / "l.json")
    assert Ledger.load(tmp_path / "l.json").forecasts[f.id].status == "final"
