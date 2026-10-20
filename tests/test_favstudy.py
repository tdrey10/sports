from sportsedge import favstudy as F
from tests.test_dogstudy import _g
from sportsedge.linestudies import annotate


def test_lock_price_band():
    assert F.is_lock_price(-120) and F.is_lock_price(-170)
    assert not F.is_lock_price(-110) and not F.is_lock_price(-171) and not F.is_lock_price(130)
    assert not F.is_lock_price(None)


def test_home_favorite_spot_and_underdog_gets_none():
    games = [_g(0, "A", "B", 100, 90)]
    annotate(games)
    assert "home favorite" in F.spots(games[0], "home", "nba")
    assert F.spots(games[0], "away", "nba") == []  # the dog is not a favorite spot


def test_favorite_off_a_loss_uses_only_earlier_games():
    games = [_g(0, "A", "B", 90, 100), _g(1, "A", "C", 100, 90)]
    annotate(games)
    assert "favorite off a loss" in F.spots(games[1], "home", "nba")
    assert "favorite off a loss" not in F.spots(games[0], "home", "nba")


def test_run_with_no_data_is_empty(tmp_path):
    assert F.run(tmp_path)["rows"] == []
