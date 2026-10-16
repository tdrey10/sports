from datetime import datetime, timedelta, timezone

from sportsedge import dogstudy as D
from sportsedge.linestudies import G


def _g(i, home, away, hs, as_):
    return G("nba", str(i), 2022, datetime(2023, 1, 1, tzinfo=timezone.utc) + timedelta(days=i), home, away,
             hs, as_, False, {"home": -150, "away": 130}, {"home": None, "away": None}, None,
             {"home": None, "away": None}, 200.0, {"over": -110, "under": -110}, {"home": 1.0, "away": 1.0},
             None, None, None, None, None, True, {"home": "", "away": ""}, "2",
             {"home": None, "away": None}, None, {"home": None, "away": None}, {"home": None, "away": None})


def test_features_use_only_earlier_games():
    games = [_g(0, "A", "B", 100, 90), _g(1, "B", "A", 100, 90), _g(2, "A", "B", 1, 0)]
    f = D.features(games)
    assert f[0]["last_winner"] is None
    assert f[1]["last_winner"] == "A"  # game 0 known before game 1
    assert f[2]["last_winner"] == "B"  # game 1 now counts; game 2's own result never leaks in
    assert f[2]["form"]["home"] is None  # fewer than 4 games played


def test_form_is_average_margin_of_last_four():
    games = [_g(i, "A", "B", 110, 100) for i in range(4)] + [_g(4, "A", "B", 0, 0)]
    f = D.features(games)
    assert f[4]["form"] == {"home": 10.0, "away": -10.0}


def test_run_with_no_data_is_empty(tmp_path):
    assert D.run(tmp_path)["rows"] == []
