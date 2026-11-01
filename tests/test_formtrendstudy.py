from sportsedge import formtrendstudy as S
from tests.test_dogstudy import _g


def _game(i, home, away, hs, as_):
    g = _g(i, home, away, hs, as_)
    g.sport, g.season = "nfl", 2024
    return g


def test_form_is_last5_minus_season_from_earlier_games_only():
    games = [_game(i, "A", "B", 10, 20) for i in range(6)]  # A loses by 10 six times
    games += [_game(6 + i, "A", "B", 30, 20) for i in range(5)]  # then wins by 10 five times
    games.append(_game(20, "A", "B", 0, 0))
    h = S.history(games)
    assert h[5]["home"] is None  # only 5 earlier games, MIN_GAMES is 6
    assert h[6]["home"] == 0.0  # last 5 equal the season average
    assert abs(h[-1]["home"] - (10 - (-60 + 50) / 11)) < 1e-9
    assert abs(h[-1]["away"] + h[-1]["home"]) < 1e-9
    later = _game(30, "A", "B", 0, 1)
    later.season = 2025
    assert S.history(games + [later])[-1] == {"home": None, "away": None}


def test_spots_tag_hot_cold_and_price_band():
    g = _game(0, "A", "B", 20, 10)
    g.ml = {"home": -140, "away": 120}
    g.spread, g.spread_odds = -2.5, {"home": -110, "away": -110}
    got = S.spots(g, {"home": 5.0, "away": -5.0}, 4.0)
    assert ("lock fav on a hot vs cold side", "home", "ml") in got
    assert ("spread on a hot side", "home", "spread") in got
    assert ("dog on a cold vs hot side", "away", "ml") in got
    assert S.spots(g, {"home": None, "away": 1.0}, 4.0) == []
    assert S.spots(g, {"home": 1.0, "away": -1.0}, 4.0) == []


def test_cover_uses_the_home_line():
    g = _game(0, "A", "B", 20, 10)
    g.spread = -7.0
    assert S.cover(g, "home") == 1.0 and S.cover(g, "away") == 0.0
    g.spread = -10.0
    assert S.cover(g, "home") == 0.5


def test_run_with_no_data_is_empty(tmp_path):
    out = S.run(tmp_path)
    assert out["rows"] == [] and out["control"]["spots"] == 0
