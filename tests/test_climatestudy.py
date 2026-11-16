"""Climate shock study (Nov 16): home climate from earlier home games only, spots for the visitor only."""

from sportsedge import climatestudy as C
from tests.test_dogstudy import _g


def _games(rows):
    out = []
    for i, (h, a, indoor, temp) in enumerate(rows):
        g = _g(i, h, a, 20, 17)
        g.sport, g.indoor, g.temp, g.wind = "nfl", indoor, temp, None
        g.start = g.start.replace(month=12)
        out.append(g)
    return out


def test_climate_uses_only_earlier_home_games():
    rows = [("D", f"X{i}", True, None) for i in range(C.MIN_HOME)] + [("C", "D", False, 25.0)]
    games = _games(rows)
    cl = C.climates(games)
    assert cl[C.MIN_HOME - 1]["home"] is None  # only MIN_HOME - 1 earlier home games so far
    assert cl[C.MIN_HOME]["away"] == "indoor"  # four earlier dome home games
    assert C.shock_spots(games[C.MIN_HOME], cl[C.MIN_HOME]) == ["cold shock", "freezing shock"]


def test_warm_and_cold_outdoor_teams():
    warm = [("W", f"X{i}", False, 70.0) for i in range(C.MIN_HOME)]
    cold = [("K", f"Y{i}", False, 20.0) for i in range(C.MIN_HOME)]
    games = _games(warm + cold + [("W", "K", False, 85.0), ("K", "W", False, 30.0)])
    cl = C.climates(games)
    n = 2 * C.MIN_HOME
    assert cl[n] == {"home": "warm", "away": "cold"}
    assert C.shock_spots(games[n], cl[n]) == ["heat shock"]
    assert C.shock_spots(games[n + 1], cl[n + 1]) == ["cold shock", "freezing shock"]


def test_no_spot_indoors_or_at_a_neutral_site():
    games = _games([("D", f"X{i}", True, None) for i in range(C.MIN_HOME)] + [("C", "D", True, 25.0)])
    cl = C.climates(games)
    assert C.shock_spots(games[-1], cl[-1]) == []  # the game itself is under a roof
    games[-1].indoor, games[-1].neutral = False, True
    assert C.shock_spots(games[-1], cl[-1]) == []


def test_bets_grade_totals_and_spreads():
    g = _games([("A", "B", False, 30.0)])[0]  # A wins 20-17, total 200 -> under
    g.spread, g.spread_odds = -2.5, {"home": -110, "away": -110}
    kinds = {k: r for k, _, r in C.bets(g)}
    assert kinds["under"] == 1.0 and kinds["over"] == 0.0
    assert kinds["host spread"] == 1.0 and kinds["visitor spread"] == 0.0
    assert kinds["host lock-price fav ML"] == 1.0 and kinds["visitor dog ML"] == 0.0
