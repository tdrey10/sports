from sportsedge import favmovestudy as S
from tests.test_dogstudy import _g


def _game(open_home, close_home, close_away, hs=100, as_=90):
    g = _g(0, "A", "B", hs, as_)
    g.sport, g.season = "nba", 2024
    g.open_ml = {"home": open_home, "away": -open_home if open_home > 0 else 130}
    g.ml = {"home": close_home, "away": close_away}
    return g


def test_move_is_home_win_chance_change():
    g = _game(-130, -150, 130)
    assert S.move(g) > 0.01
    g.open_ml = {"home": None, "away": None}
    assert S.move(g) is None


def test_lock_favorite_that_rose_is_labelled_and_the_dog_side_fell():
    labels = S.spots(_game(-125, -150, 130))
    assert ("lock fav win chance rose 1+ pts since open", "home") in labels
    assert ("dog win chance fell 1+ pts since open", "away") in labels


def test_prices_outside_the_bands_are_skipped():
    assert S.spots(_game(-300, -400, 320)) == []


def test_run_with_no_data_is_empty(tmp_path):
    out = S.run(tmp_path)
    assert out["rows"] == [] and out["control"]["spots"] == 0
