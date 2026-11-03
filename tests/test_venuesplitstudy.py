from sportsedge import venuesplitstudy as S
from tests.test_dogstudy import _g


def _game(i, home, away, hs, as_):
    g = _g(i, home, away, hs, as_)
    g.sport, g.season = "nfl", 2024
    return g


def test_split_uses_earlier_games_only_and_needs_venue_games():
    # A wins big at home (+10) every time, loses at B's place by 0 -> strong at home vs overall
    games = [_game(i, "A", "B", 20, 10) for i in range(3)]
    games += [_game(10 + i, "B", "A", 10, 10) for i in range(3)]  # A road ties, margin 0
    games.append(_game(20, "A", "B", 0, 0))
    h = S.history(games)
    assert h[0]["home"] is None and h[5]["home"] is None  # not enough games yet
    assert h[6]["home"] == 5.0  # home avg +10 minus overall avg +5
    later = _game(30, "A", "B", 1, 0)
    later.season = 2025
    assert S.history(games + [later])[-1] == {"home": None, "away": None}


def test_spots_tag_strong_and_weak():
    g = _game(0, "A", "B", 20, 10)
    g.ml = {"home": -140, "away": 120}
    g.spread, g.spread_odds = -2.5, {"home": -110, "away": -110}
    got = S.spots(g, {"home": 5.0, "away": -5.0})
    assert ("lock fav, home strong vs weak at the venue", "home", "ml") in got
    assert ("spread, home strong at the venue", "home", "spread") in got
    assert ("dog, away weak vs strong at the venue", "away", "ml") in got
    assert S.spots(g, {"home": None, "away": 3.0}) == []
    assert S.spots(g, {"home": 0.1, "away": -0.1}) == []


def test_run_with_no_data_is_empty(tmp_path):
    out = S.run(tmp_path)
    assert out["rows"] == [] and out["control"]["spots"] == 0
