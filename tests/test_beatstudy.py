from sportsedge import beatstudy as B
from tests.test_dogstudy import _g


def test_form_counts_only_earlier_games_and_needs_min_games():
    games = [_g(i, "A", f"O{i}", 1, 0) for i in range(8)]
    for g in games:
        g.ml = {"home": -200, "away": 170}  # A is a favorite and wins every time
    f = B.form_vs_price(games, "nfl")  # MIN_GAMES 5
    assert f[4]["home"] is None  # only 4 earlier games
    assert f[5]["home"] > 0.2  # won every game at ~66% expectation
    assert f[7]["away"] is None  # opponents are new each game


def test_new_season_resets():
    games = [_g(i, "A", f"O{i}", 1, 0) for i in range(7)]
    for g in games:
        g.ml = {"home": -200, "away": 170}
    games[6].season = 2023
    assert B.form_vs_price(games, "nfl")[6]["home"] is None


def test_spots_only_in_price_bands():
    g = _g(0, "A", "B", 1, 0)
    g.ml = {"home": -140, "away": 120}
    form = {"home": -0.1, "away": 0.1}
    assert "dog: facing a team missing its prices" in B.spots(g, "away", "nfl", form)
    assert "lock favorite: missing its prices this season" in B.spots(g, "home", "nfl", form)
    g.ml = {"home": -400, "away": 320}
    assert B.spots(g, "away", "nfl", form) == []


def test_run_with_no_data_is_empty(tmp_path):
    assert B.run(tmp_path)["rows"] == []
