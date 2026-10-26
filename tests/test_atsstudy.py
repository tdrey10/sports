from sportsedge import atsstudy as A
from tests.test_dogstudy import _g


def _game(i, home, away, hs, as_, spread):
    g = _g(i, home, away, hs, as_)
    g.spread = spread
    return g


def test_cover_margin_signs():
    g = _game(0, "A", "B", 110, 100, -7.0)  # home favored by 7, wins by 10: covers by 3
    assert A.cover_margin(g, "home") == 3.0 and A.cover_margin(g, "away") == -3.0


def test_prior_uses_only_earlier_games():
    games = [_game(0, "A", "B", 130, 100, -2.0), _game(1, "A", "C", 100, 100, -2.0)]
    p = A.annotate_cover(games)
    assert p[0] == {"home": None, "away": None}
    assert p[1]["home"] == 28.0 and p[1]["away"] is None  # game 0's cover known; game 1's own never leaks


def test_spots_label_own_and_opponent():
    assert A.spots({"home": 12.0, "away": -11.0}, "home", "nba") == [
        "own last game covered by 10+", "opponent last game missed by 10+"]
    assert A.spots({"home": 3.0, "away": None}, "home", "nba") == []


def test_run_with_no_data_is_empty(tmp_path):
    assert A.run(tmp_path)["rows"] == []
