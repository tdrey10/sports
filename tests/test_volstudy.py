"""Margin volatility study (Nov 13): earlier games only, tuning-season cutoffs, each season starts clean."""

from sportsedge import volstudy as V
from tests.test_dogstudy import _g


def _game(i, home, away, hs, as_, spread=None, sport="nfl", season=2020):
    g = _g(i, home, away, hs, as_)
    g.sport, g.season, g.spread = sport, season, spread
    return g


def test_volatility_reads_earlier_games_only_against_the_spread():
    V.MIN_GAMES["nfl"], keep = 2, V.MIN_GAMES["nfl"]
    try:
        games = [_game(0, "A", "B", 30, 10, spread=-10.0),  # A +20 vs expected +10: +10
                 _game(1, "A", "C", 10, 10, spread=-10.0),  # A 0 vs expected +10: -10
                 _game(2, "A", "B", 50, 0, spread=-3.0)]
        v = V.volatility(games, "nfl")
        assert v[0] == {"home": None, "away": None} and v[1]["home"] is None  # too few earlier games
        assert round(v[2]["home"], 3) == round((2 * 10 ** 2) ** 0.5, 3)  # sd of (+10, -10); game 2 never leaks in
        assert v[2]["away"] is None  # B has one earlier game
        nxt = V.volatility(games + [_game(3, "A", "B", 1, 0, spread=-1.0, season=2021)], "nfl")
        assert nxt[3]["home"] is None  # a new season starts clean
        assert V.volatility([_game(0, "A", "B", 3, 0)], "nfl")[0]["home"] is None
    finally:
        V.MIN_GAMES["nfl"] = keep


def test_mlb_uses_the_raw_margin_and_spots_follow_the_cutoffs():
    assert V._result(_game(0, "A", "B", 5, 2, sport="mlb"), "away", "mlb") == -3
    assert V._result(_game(0, "A", "B", 5, 2, spread=-1.5), "home", "nfl") == 1.5
    cut = (5.0, 12.0)
    assert V.spots({"home": 13.0, "away": 4.0}, "home", cut) == ["own volatile", "opponent steady", "volatile vs steady"]
    assert V.spots({"home": 13.0, "away": 4.0}, "away", cut) == ["own steady", "opponent volatile"]
    assert V.spots({"home": 8.0, "away": None}, "home", cut) == []


def test_cutoffs_never_see_blind_seasons():
    games = [_game(i, "A", "B", 1, 0, season=2020 if i < 50 else 2023) for i in range(100)]
    vols = [{"home": float(i), "away": None} if i < 50 else {"home": 1000.0, "away": None} for i in range(100)]
    lo, hi = V.cutoffs(games, vols)
    assert hi < 50  # the 2023 values (1000) never move the cutoff
