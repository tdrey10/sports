from sportsedge import finishstudy as F
from tests.test_dogstudy import _g


def _game(i, home, away, hs, as_, h1h, h1a, season=2024):
    g = _g(i, home, away, hs, as_)
    g.sport, g.season = "nba", season
    g.h1 = {"home": h1h, "away": h1a}
    return g


def test_second_half_margin_and_prior_is_earlier_only():
    g0 = _game(0, "A", "B", 100, 90, 40, 50)  # A trailed by 10 at half, won by 10: 2H +20
    assert F.second_half(g0, "home") == 20 and F.second_half(g0, "away") == -20
    g1 = _game(1, "A", "C", 50, 100, 25, 50)
    p = F.last_finish([g0, g1])
    assert p[0] == {"home": None, "away": None}
    assert p[1]["home"] == 20 and p[1]["away"] is None  # ignores game 1's own result
    g2 = _game(2, "A", "B", 1, 0, 0, 0, season=2025)
    assert F.last_finish([g0, g2])[1]["home"] is None


def test_missing_first_half_is_none():
    g = _game(0, "A", "B", 100, 90, None, None)
    assert F.second_half(g, "home") is None


def test_spots_and_empty_run(tmp_path):
    s = F.spots({"home": 12, "away": -9}, "home", -5, 5)
    assert "own closed last game strong (top-quarter 2nd half)" in s
    assert "opponent faded last game (bottom-quarter 2nd half)" in s
    assert F.spots({"home": None, "away": 0}, "home", -5, 5) == []
    assert F.run(tmp_path)["rows"] == []
