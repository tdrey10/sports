"""Lookahead / sandwich study (Nov 14): records from earlier games only, next-game opponents judged on today's records."""

from sportsedge import lookstudy as L
from tests.test_dogstudy import _g


def _season(rows, sport="nfl"):
    games = []
    for i, (h, a, hs, as_) in enumerate(rows):
        g = _g(i, h, a, hs, as_)
        g.sport, g.season = sport, 2020
        games.append(g)
    return games


def test_records_use_only_earlier_games_and_reset_each_season():
    L.MIN_GAMES["nfl"], keep = 1, L.MIN_GAMES["nfl"]
    try:
        games = _season([("A", "B", 1, 0), ("A", "C", 1, 0), ("B", "C", 0, 1)])
        snaps = L.records_before(games, "nfl")
        assert snaps[0] == {}
        assert snaps[1] == {"A": 1.0, "B": 0.0}  # game 1's own result never leaks in
        assert snaps[2]["A"] == 1.0 and snaps[2]["C"] == 0.0
        nxt = _season([("A", "B", 1, 0)])
        nxt[0].season = 2021
        assert L.records_before(games + nxt, "nfl")[3] == {}  # a new season starts clean
    finally:
        L.MIN_GAMES["nfl"] = keep


def test_lookahead_and_lookback_flags_read_the_neighbouring_games():
    L.MIN_GAMES["nfl"], keep = 1, L.MIN_GAMES["nfl"]
    try:
        # S is strong (won twice), W weak (lost twice) before game 4; T plays W (soft) between S games.
        games = _season([("S", "X", 1, 0), ("W", "S", 0, 1), ("W", "Y", 0, 1),
                         ("T", "S", 1, 0), ("T", "W", 1, 0), ("S", "T", 1, 0)])
        snaps, nb = L.records_before(games, "nfl"), L.neighbours(games)
        cut = (0.5, 0.6)  # weak <= .500, strong >= .600
        assert nb[4]["home"] == ((3, "home"), (5, "away"))
        own = L.side_spots(games, 4, "home", nb, snaps, cut)
        assert own["lookback"] and own["lookahead"] and own["sandwich"]
        assert L.spots(games, 4, "away", nb, snaps, cut) == ["opp lookahead", "opp sandwich", "opp lookback"]
        assert L.spots(games, 0, "home", nb, snaps, cut) == []  # no records yet
    finally:
        L.MIN_GAMES["nfl"] = keep


def test_cutoffs_never_see_blind_seasons():
    games = _season([("A", "B", 1, 0)] * 100)
    for i, g in enumerate(games):
        g.season = 2020 if i < 50 else 2023
    strong = [{"home": i / 100, "away": None} if i < 50 else {"home": 1.0, "away": None} for i in range(100)]
    weak, hi = L.cutoffs(games, strong)
    assert hi < 0.5
