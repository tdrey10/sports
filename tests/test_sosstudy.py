from sportsedge import sosstudy as S
from tests.test_dogstudy import _g


def _game(i, home, away, hs, as_):
    g = _g(i, home, away, hs, as_)
    g.sport, g.season = "nfl", 2024
    return g


def test_history_uses_earlier_games_only_and_resets_by_season():
    games = [_game(i, "A", "X", 10, 0) for i in range(6)]  # X loses by 10 to A every time
    games += [_game(10 + i, "B", "Y", 0, 10) for i in range(6)]  # Y beats B by 10
    games.append(_game(20, "A", "B", 3, 0))
    h = S.history(games)
    assert h[0] == {"home": None, "away": None}
    assert h[-1]["home"] == -10.0  # A faced X (avg margin -10) only
    assert h[-1]["away"] == 10.0  # B faced Y, who beat B by 10 every time
    later = _game(30, "A", "B", 1, 0)
    later.season = 2025
    assert S.history(games + [later])[-1] == {"home": None, "away": None}


def test_spots_tag_tougher_and_easier():
    g = _game(0, "A", "B", 20, 10)
    g.ml = {"home": -140, "away": 120}
    g.spread, g.spread_odds = -2.5, {"home": -110, "away": -110}
    got = S.spots(g, {"home": 4.0, "away": 0.0})
    assert ("lock fav, home tougher schedule", "home", "ml") in got
    assert ("dog, away easier schedule", "away", "ml") in got
    assert ("spread, home tougher schedule", "home", "spread") in got
    assert S.spots(g, {"home": None, "away": 3.0}) == []
    assert S.spots(g, {"home": 0.5, "away": 0.0}) == []


def test_run_with_no_data_is_empty(tmp_path):
    out = S.run(tmp_path)
    assert out["rows"] == [] and out["control"]["spots"] == 0
