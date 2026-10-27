from sportsedge import bouncestudy as B
from tests.test_dogstudy import _g


def test_margins_use_only_earlier_games_and_reset_by_season():
    games = [_g(0, "A", "B", 120, 90), _g(1, "B", "A", 100, 95), _g(2, "A", "C", 1, 0)]
    m = B.last_margins(games)
    assert m[0] == {"home": None, "away": None}
    assert m[1] == {"home": -30, "away": 30}  # B lost by 30, A won by 30 in game 0
    assert m[2] == {"home": -5, "away": None}  # A lost game 1 as the road side; C has no earlier game
    games[2].season = 2023
    assert B.last_margins(games)[2] == {"home": None, "away": None}


def test_spots_label_blowouts_in_price_band_only():
    games = [_g(0, "A", "B", 120, 90), _g(1, "B", "A", 100, 95)]
    games[1].ml = {"home": -140, "away": 120}
    m = B.last_margins(games)
    assert "dog: off a blowout win (letdown)" in B.spots(games[1], "away", "nba", m[1])
    got = B.spots(games[1], "home", "nba", m[1])
    assert "lock favorite: off a blowout loss (bounce back)" in got
    assert "lock favorite: facing a team off a blowout win" in got
    games[1].ml = {"home": -400, "away": 320}
    assert B.spots(games[1], "home", "nba", m[1]) == []
