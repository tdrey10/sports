from sportsedge import reststudy as R
from sportsedge.linestudies import annotate
from tests.test_dogstudy import _g


def test_result_scores_unders_overs_and_pushes():
    g = _g(0, "A", "B", 100, 90)  # 190 vs total 200
    assert R.result(g) == (1.0, 0.0)
    g.hs = 110  # 200 pushes
    assert R.result(g) == (0.5, 0.5)
    g.total_odds["under"] = None
    assert R.result(g) is None


def test_spots_need_known_rest_and_split_by_sport():
    g = _g(0, "A", "B", 100, 90)
    annotate([g])
    g.rest = {"home": None, "away": 1.0}
    assert R.spots(g, "nba") == []
    g.rest = {"home": 1.0, "away": 1.0}
    got = [s for s, _ in R.spots(g, "nba")]
    assert "under: both on a back-to-back" in got and "over: a team on a back-to-back" in got
    g.rest = {"home": 10.0, "away": 12.0}
    assert ("under: both off a long break or bye", "under") in R.spots(g, "nfl")
    assert all("back-to-back" not in s for s, _ in R.spots(g, "nfl"))


def test_run_with_no_data_is_empty(tmp_path):
    out = R.run(tmp_path)
    assert out["rows"] == [] and out["control"]["spots"] == 0
