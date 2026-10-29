from sportsedge import scriptstudy as S
from tests.test_dogstudy import _g


def test_quartiles_use_tuning_seasons_only_and_need_enough_games():
    games = [_g(i, "A", "B", 1, 1) for i in range(10)]
    assert S.quartiles(games) is None
    for i, g in enumerate(games * 5):
        g.total = 100 + i % 10
    big = [_g(i, "A", "B", 1, 1) for i in range(50)]
    for i, g in enumerate(big):
        g.total = 100 + i
        g.season = 2018
    late = _g(99, "A", "B", 1, 1)
    late.total, late.season = 9999, 2024
    q = S.quartiles(big + [late])
    assert q == (112, 137)


def test_spots_label_total_level_and_spread_size():
    g = _g(0, "A", "B", 100, 90)
    g.total, g.spread = 200, -12
    got = [s for s, _ in S.spots(g, "nba", (210, 230))]
    assert "under: low total for the sport" in got and "over: big spread (blowout expected)" in got
    g.spread = 1.0
    assert ("under: pick'em spread", "under") in S.spots(g, "nba", None)
    assert S.spots(g, "mlb", None) == []


def test_run_with_no_data_is_empty(tmp_path):
    out = S.run(tmp_path)
    assert out["rows"] == [] and out["control"]["spots"] == 0
