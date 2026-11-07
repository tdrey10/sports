from sportsedge import atsratestudy as S
from tests.test_dogstudy import _g


def _game(i, home, away, hs, as_, spread=-3.0):
    g = _g(i, home, away, hs, as_)
    g.sport, g.season, g.spread = "nfl", 2024, spread
    return g


def test_rate_uses_earlier_games_only_and_needs_min_games():
    # A (home, favored -3) wins by 10 each time -> covers every game
    games = [_game(i, "A", "B", 30, 20) for i in range(6)]
    games.append(_game(10, "A", "B", 0, 99))
    r = S.annotate_rate(games)
    assert r[0]["home"] is None and r[5]["home"] is None
    assert r[6]["home"] == 1.0 and r[6]["away"] == 0.0  # ignores game 6's own result
    later = _game(30, "A", "B", 1, 0)
    later.season = 2025
    assert S.annotate_rate(games + [later])[-1]["home"] is None


def test_spots_follow_fade_and_gap():
    s = S.spots({"home": 0.7, "away": 0.3}, "home")
    assert "own side covering 60%+ (follow)" in s
    assert "own cover rate 20+ pts above opponent's" in s
    assert any(x.startswith("opponent covering under 40%") for x in s)
    assert S.spots({"home": None, "away": 0.5}, "home") == []


def test_run_with_no_data_is_empty(tmp_path):
    out = S.run(tmp_path)
    assert out["rows"] == [] and out["control"]["spots"] == 0
