from sportsedge import closegamestudy as C
from tests.test_dogstudy import _g


def _game(i, home, away, hs, as_, sport="nba", season=2024):
    g = _g(i, home, away, hs, as_)
    g.sport, g.season = sport, season
    return g


def test_prior_marks_close_wins_and_losses_from_earlier_games_only():
    g0 = _game(0, "A", "B", 100, 98)  # A wins by 2 (close), B loses close
    g1 = _game(1, "A", "C", 100, 80)  # blowout
    g2 = _game(2, "B", "A", 90, 90 + 2)
    p = C.last_close([g0, g1, g2], "nba")
    assert p[0] == {"home": None, "away": None}  # nothing earlier
    assert p[1]["home"] == "win" and p[1]["away"] is None
    assert p[2]["home"] == "loss" and p[2]["away"] == "other"  # A's last game was the blowout


def test_mlb_close_means_one_run_and_season_resets():
    g0 = _game(0, "A", "B", 3, 2, sport="mlb")
    g1 = _game(1, "A", "B", 5, 3, sport="mlb")
    assert C.last_close([g0, g1], "mlb")[1]["home"] == "win"
    g2 = _game(2, "A", "C", 4, 1, sport="mlb")
    assert C.last_close([g1, g2], "mlb")[1]["home"] == "other"  # 2 runs is not close in MLB
    g3 = _game(3, "A", "B", 1, 0, sport="mlb", season=2025)
    assert C.last_close([g0, g3], "mlb")[1]["home"] is None


def test_spots_and_empty_run(tmp_path):
    s = C.spots({"home": "win", "away": "other"}, "away")
    assert s == ["opponent last game was a close win"]
    assert C.spots({"home": None, "away": None}, "home") == []
    assert C.run(tmp_path)["rows"] == []
