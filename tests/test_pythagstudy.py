from sportsedge import pythagstudy as S
from tests.test_dogstudy import _g


def _game(i, home, away, hs, as_):
    g = _g(i, home, away, hs, as_)
    g.sport, g.season = "nfl", 2024
    return g


def test_luck_is_win_pct_minus_pythagorean():
    assert S.luck_of(0, 0, 0, 0, 2.37) is None
    assert abs(S.luck_of(5, 10, 20, 20, 2.0)) < 1e-9  # .500 and even points: no luck
    assert S.luck_of(8, 10, 20, 20, 2.0) > 0.29  # 8-2 on even points is lucky


def test_history_uses_earlier_games_only_and_resets_by_season():
    games = [_game(i, "A", "B", 21, 20) for i in range(6)]  # A wins six by one point
    games.append(_game(10, "A", "B", 0, 0))
    h = S.history(games)
    assert h[5]["home"] is None  # only 5 earlier games
    assert h[6]["home"] > 0.3 and abs(h[6]["home"] + h[6]["away"]) < 0.05
    later = _game(30, "A", "B", 1, 0)
    later.season = 2025
    assert S.history(games + [later])[-1] == {"home": None, "away": None}


def test_spots_tag_lucky_unlucky_and_price_band():
    g = _game(0, "A", "B", 20, 10)
    g.ml = {"home": -140, "away": 120}
    g.spread, g.spread_odds = -2.5, {"home": -110, "away": -110}
    got = S.spots(g, {"home": 0.2, "away": -0.2})
    assert ("lock fav on a lucky vs unlucky side", "home", "ml") in got
    assert ("spread on a lucky side", "home", "spread") in got
    assert ("dog on a unlucky vs lucky side", "away", "ml") in got
    assert S.spots(g, {"home": None, "away": 0.3}) == []
    assert S.spots(g, {"home": 0.01, "away": -0.01}) == []


def test_run_with_no_data_is_empty(tmp_path):
    out = S.run(tmp_path)
    assert out["rows"] == [] and out["control"]["spots"] == 0
