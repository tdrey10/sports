from sportsedge import recordstudy as R
from tests.test_dogstudy import _g


def _game(i, home, away, hs, as_, sport="nfl", season=2024):
    g = _g(i, home, away, hs, as_)
    g.sport, g.season = sport, season
    return g


def test_records_use_earlier_games_only_and_need_min_games():
    gs = [_game(0, "A", "B", 20, 10), _game(1, "A", "C", 10, 20), _game(2, "B", "C", 7, 7),
          _game(3, "A", "B", 30, 0)]
    p = R.season_records(gs, "nfl")  # NFL needs 3 earlier games
    assert p[0] == {"home": None, "away": None}
    assert p[2] == {"home": None, "away": None}  # B and C have 1 game each
    gs.append(_game(4, "A", "C", 1, 2))  # A has 3 earlier games (2-1), C has 2
    p = R.season_records(gs, "nfl")
    assert abs(p[4]["home"] - 2 / 3) < 1e-9 and p[4]["away"] is None


def test_tie_counts_half_and_season_resets():
    gs = [_game(i, "A", "B", 7, 7) for i in range(3)] + [_game(3, "A", "B", 1, 0)]
    p = R.season_records(gs, "nfl")
    assert p[3] == {"home": 0.5, "away": 0.5}
    gs.append(_game(4, "A", "B", 1, 0, season=2025))
    assert R.season_records(gs, "nfl")[4] == {"home": None, "away": None}


def test_spots_and_empty_run(tmp_path):
    s = R.spots({"home": 0.7, "away": 0.4}, "home")
    assert s == ["better record than the opponent", "record 20+ pts better", "winning record vs a losing one"]
    assert R.spots({"home": 0.7, "away": 0.4}, "away") == ["record 20+ pts worse", "losing record vs a winning one"]
    assert R.spots({"home": None, "away": 0.4}, "home") == []
    assert R.run(tmp_path)["rows"] == []
