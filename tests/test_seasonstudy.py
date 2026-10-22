from datetime import datetime, timezone

from sportsedge import seasonstudy as S
from tests.test_dogstudy import _g


def _game(sport, ml, played):
    g = _g(0, "A", "B", 1, 0)
    g.sport = sport
    g.start = datetime(2026, 10, 21, 23, 0, tzinfo=timezone.utc)
    g.ml = ml
    g.ctx = {"home": {"played": played}, "away": {"played": played}}
    return g


def test_dog_gets_month_and_phase():
    g = _game("nba", {"home": -140, "away": 120}, 5)
    assert S.spots(g, "away", "nba") == ["dog: month 10", "dog: first 10 games"]


def test_lock_favorite_phase_and_other_prices_skipped():
    g = _game("nfl", {"home": -140, "away": 120}, 10)
    assert "lock favorite: games 10+" in S.spots(g, "home", "nfl")
    g = _game("nfl", {"home": -400, "away": 320}, 10)
    assert S.spots(g, "home", "nfl") == [] and S.spots(g, "away", "nfl") == []
