"""Series spots study (Nov 15): series built from earlier games only, broken by a gap, a new opponent or a new season."""

from datetime import timedelta

from sportsedge import seriesstudy as S
from tests.test_dogstudy import _g


def _games(rows, sport="mlb"):
    out = []
    for i, (h, a, hs, as_) in enumerate(rows):
        g = _g(i, h, a, hs, as_)  # one game a day
        g.sport, g.season = sport, 2020
        out.append(g)
    return out


def test_series_runs_use_only_earlier_games():
    games = _games([("A", "B", 1, 3), ("A", "B", 2, 4), ("A", "B", 9, 1), ("A", "C", 1, 0)])
    st = S.series_state(games, "mlb")
    assert st[0] == {"home": [], "away": []}
    assert st[1] == {"home": [-2], "away": [2]}  # game 1's own result never leaks in
    assert st[2] == {"home": [-2, -2], "away": [2, 2]}
    assert st[3] == {"home": [], "away": []}  # a new opponent ends the series
    assert S.spots(st[2], "home", "mlb") == ["opp won last", "own lost last", "own facing sweep", "opp going for sweep"]


def test_gap_and_new_season_break_a_series():
    games = _games([("A", "B", 1, 0), ("A", "B", 1, 0)])
    games[1].start = games[0].start + timedelta(days=S.GAP_DAYS["mlb"] + 1)
    assert S.series_state(games, "mlb")[1] == {"home": [], "away": []}
    games = _games([("A", "B", 1, 0), ("A", "B", 1, 0)])
    games[1].season = 2021
    assert S.series_state(games, "mlb")[1] == {"home": [], "away": []}


def test_big_loss_flag_uses_the_sport_cutoff():
    assert S.side_flags([-5], "mlb")["lost last big"]
    assert not S.side_flags([-4], "mlb")["lost last big"]
    assert S.side_flags([], "mlb") == {}
    assert not S.side_flags([-3], "mlb")["facing sweep"]  # needs 2+ earlier games of the series
