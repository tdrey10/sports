from datetime import timedelta

from sportsedge import starterstudy as S
from tests.test_dogstudy import _g


def _game(i, home, away, sp_home, sp_away, sport="nhl", season=2024, days=None):
    g = _g(i, home, away, 3, 2)
    g.sport, g.season = sport, season
    if days is not None:
        g.start = _g(0, "x", "y", 0, 0).start + timedelta(days=days)
    g.starter = {"home": sp_home, "away": sp_away}
    return g


def test_nhl_back_to_back_and_long_layoff_use_earlier_starts_only():
    g0 = _game(0, "A", "B", "Gh", "Gb", days=0)
    g1 = _game(1, "A", "C", "Gh", "Gc", days=1)  # A's goalie again the next day
    g2 = _game(2, "A", "D", "Gh", "Gd", days=10)  # 9 days off
    c = S.starter_context([g0, g1, g2])
    assert c[0] == {"home": [], "away": []}  # nothing earlier
    assert c[1]["home"] == [S.SHORT["nhl"]] and c[1]["away"] == []
    assert c[2]["home"] == [S.LONG["nhl"]]


def test_rematch_within_14_days_and_season_reset():
    g0 = _game(0, "A", "B", "P1", "P2", sport="mlb", days=0)
    g1 = _game(1, "B", "A", "P2", "P1", sport="mlb", days=6)  # same two starters, same teams
    g2 = _game(2, "A", "B", "P1", "P2", sport="mlb", days=40)  # too long ago (and a long gap)
    c = S.starter_context([g0, g1, g2])
    assert c[1]["home"] == [S.REMATCH] and c[1]["away"] == [S.REMATCH]
    assert S.REMATCH not in c[2]["home"] and S.LONG["mlb"] in c[2]["home"]
    g3 = _game(3, "A", "B", "P1", "P2", sport="mlb", season=2025, days=45)
    assert S.starter_context([g0, g3])[1] == {"home": [], "away": []}  # new season: no history


def test_same_name_on_another_team_is_a_different_starter():
    g0 = _game(0, "A", "B", "Smith", "X", days=0)
    g1 = _game(1, "C", "D", "Smith", "Y", days=1)
    assert S.starter_context([g0, g1])[1]["home"] == []


def test_spots_and_empty_run(tmp_path):
    ctx = {"home": [S.REMATCH], "away": []}
    assert S.spots(ctx, "away") == [f"opponent {S.REMATCH}"]
    assert S.spots(ctx, "home") == [f"own {S.REMATCH}"]
    assert S.run(tmp_path)["rows"] == []
