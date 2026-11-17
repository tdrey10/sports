"""Market overreaction study (Nov 17): jumps read earlier closing prices only, cutoffs are tuning-only."""

from sportsedge import jumpstudy as J
from tests.test_dogstudy import _g


def _game(i, home, away, ml_h, ml_a, hs=1, as_=0, season=2020, sport="nfl"):
    g = _g(i, home, away, hs, as_)
    g.sport, g.season, g.neutral = sport, season, False
    g.ml = {"home": ml_h, "away": ml_a}
    return g


def test_jump_is_todays_price_against_the_markets_own_earlier_prices():
    J.MIN_GAMES["nfl"], keep = 1, J.MIN_GAMES["nfl"]
    try:
        games = [_game(0, "A", "B", -110, -110), _game(1, "A", "B", -110, -110),
                 _game(2, "A", "B", -300, 240, hs=0, as_=50)]
        js = J.jumps(games, "nfl")
        assert js[0]["home"] is None  # nobody has a game yet
        assert abs(js[1]["home"]) < 1e-9 and js[1]["away"] == -js[1]["home"]  # same price, no jump
        assert js[2]["home"] > 0.8 and js[2]["away"] == -js[2]["home"]  # market jumped toward A
        # Game 2's lopsided score never leaks into its own jump or last result.
        assert js[2]["last"] == {"home": 1.0, "away": 0.0}
        nxt = J.jumps(games + [_game(3, "A", "B", -110, -110, season=2021)], "nfl")
        assert nxt[3]["home"] is None and nxt[3]["last"]["home"] is None  # a new season starts counting again
        assert J.jumps([_game(0, "A", "B", None, None)], "nfl")[0]["home"] is None
    finally:
        J.MIN_GAMES["nfl"] = keep


def test_spots_follow_the_cutoffs_and_last_result():
    cut = (-0.3, 0.3)
    row = {"home": 0.5, "away": -0.5, "last": {"home": 1.0, "away": 0.0}}
    assert J.spots(row, "home", cut) == ["market jumped toward", "jumped toward after a win"]
    assert J.spots(row, "away", cut) == ["market jumped against", "jumped against after a loss"]
    assert J.spots({"home": 0.1, "away": -0.1, "last": {}}, "home", cut) == []
    assert J.spots({"home": None, "away": None, "last": {}}, "home", cut) == []


def test_cutoffs_never_see_blind_seasons():
    games = [_game(i, "A", "B", -110, -110, season=2020 if i < 50 else 2023) for i in range(100)]
    js = [{"home": float(i) if i < 50 else 1000.0, "away": None, "last": {}} for i in range(100)]
    lo, hi = J.cutoffs(games, js)
    assert hi < 50
