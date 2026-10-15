from datetime import datetime, timedelta, timezone

from sportsedge import scoringstudy as S
from sportsedge.linestudies import G


def _g(i, home, away, hs, as_, total=200.0):
    return G("nba", str(i), 2022, datetime(2023, 1, 1, tzinfo=timezone.utc) + timedelta(days=i), home, away,
             hs, as_, False, {"home": -150, "away": 130}, {"home": None, "away": None}, None,
             {"home": None, "away": None}, total, {"over": -110, "under": -110}, {"home": 1.0, "away": 1.0},
             None, None, None, None, None, True, {"home": "", "away": ""}, "2",
             {"home": None, "away": None}, None, {"home": None, "away": None}, {"home": None, "away": None})


def test_features_use_only_earlier_games():
    games = [_g(i, "A", "B", 110, 110) for i in range(3)] + [_g(3, "A", "B", 90, 90), _g(4, "A", "B", 100, 100)]
    f = S.features(games)
    assert f[0] is None and abs(f[3]["over_last"] - 0.1) < 1e-9  # last game 220 vs 200, known before game 3
    assert abs(f[4]["over_last"] + 0.1) < 1e-9  # game 3 (180) now counts; its own result never leaks into f[3]
    assert abs(f[3]["form"] - 0.1) < 1e-9


def test_result_needs_both_total_prices():
    g = _g(0, "A", "B", 100, 99)
    assert S._result(g) == (1.0, 0.0)
    g.total_odds["over"] = None
    assert S._result(g) is None


def test_run_with_no_data_is_empty(tmp_path):
    assert S.run(tmp_path)["rows"] == []
