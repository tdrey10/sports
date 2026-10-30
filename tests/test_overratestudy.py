from sportsedge import overratestudy as S
from tests.test_dogstudy import _g


def _game(i, home, away, pts, total=50.0):
    g = _g(i, home, away, pts, 0)
    g.sport, g.season, g.total = "nfl", 2024, total
    g.total_odds = {"over": -110, "under": -110}
    return g


def test_history_uses_only_earlier_games_and_resets_each_season():
    games = [_game(i, "A", "B", 60) for i in range(7)]  # every game goes over 50
    h = S.history(games)
    assert h[0]["meet"] is None and h[0]["home"] is None
    assert h[1]["meet"] == 1.0
    assert h[5]["home"] is None  # 5 earlier games < 6
    assert h[6]["home"] == 1.0 and h[6]["away"] == 1.0
    later = _game(9, "A", "B", 10)
    later.season = 2025
    assert S.history(games + [later])[-1] == {"home": None, "away": None, "meet": None}


def test_push_leaves_no_meeting_result():
    games = [_game(0, "A", "B", 50), _game(1, "A", "B", 40)]
    assert S.history(games)[1]["meet"] is None


def test_spots_label_team_rates_and_rematches():
    got = [s for s, _ in S.spots({"home": 0.7, "away": 0.65, "meet": 0.0})]
    assert "over: both teams' games run over" in got and "under: last meeting went under" in got
    assert S.spots({"home": 0.7, "away": 0.3, "meet": None}) == []


def test_run_with_no_data_is_empty(tmp_path):
    out = S.run(tmp_path)
    assert out["rows"] == [] and out["control"]["spots"] == 0
