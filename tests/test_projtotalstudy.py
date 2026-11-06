from sportsedge import projtotalstudy as S
from tests.test_dogstudy import _g


def _game(i, home, away, hs, as_):
    g = _g(i, home, away, hs, as_)
    g.sport, g.season = "nfl", 2024
    return g


def test_history_uses_earlier_games_only_and_resets_by_season():
    games = [_game(i, "A", "B", 30, 20) for i in range(6)]
    games.append(_game(10, "A", "B", 99, 99))
    h = S.history(games)
    assert h[0] is None and h[5] is None
    assert h[6] == 50.0  # A scores 30 allows 20; B scores 20 allows 30 -> 50, ignores game 6 itself
    later = _game(30, "A", "B", 1, 0)
    later.season = 2025
    assert S.history(games + [later])[-1] is None


def test_spots_over_under_and_fades():
    g = _game(0, "A", "B", 20, 10)
    g.total = 40.0
    labels = {s for _, s in S.spots(g, 46.0)}
    assert labels == {"over", "under"}
    assert [l for l, _ in S.spots(g, 46.0)][0].startswith("over")
    assert S.spots(g, 41.0) == [] and S.spots(g, None) == []


def test_run_with_no_data_is_empty(tmp_path):
    out = S.run(tmp_path)
    assert out["rows"] == [] and out["control"]["spots"] == 0
