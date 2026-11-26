import csv
import gzip
import json

from sportsedge import footluckstudy as S
from tests.test_dogstudy import _g


def _side(pts, yards, tov):
    return {"pts": pts, "yards": yards, "tov": tov}


def _box(tov_h, tov_a, pts_h=30.0, pts_a=20.0):
    return {"home": _side(pts_h, 300.0, tov_h), "away": _side(pts_a, 300.0, tov_a)}


def _games(n, season=2024):
    out = []
    for i in range(n):
        g = _g(i, "A", "B", 30, 20)
        g.season = season
        out.append(g)
    return out


def test_game_luck_turnovers_and_finishing():
    tov, fin, tot = S.game_luck(_side(30, 300, 0), _side(20, 400, 2), 0.1)
    assert tov == 2  # opponent gave it away twice, we never did
    assert fin == (30 - 30) - (20 - 40) == 20
    assert tot == 0 + -20


def test_ppy_uses_tuning_games_only():
    games = _games(2) + _games(1, season=2025)
    games[-1].gid = "x"
    box = {g.gid: _box(0, 0) for g in games}
    box["x"] = {"home": _side(600, 300, 0), "away": _side(600, 300, 0)}
    assert S.ppy(games, box) == 50 / 600


def test_features_use_only_earlier_games_and_need_three():
    games = _games(5)
    box = {g.gid: _box(0.0, 2.0) for g in games}
    box["4"] = _box(9.0, 0.0)  # game 4's own turnovers must not leak into game 4
    f = S.features(games, box, 0.1)
    assert f["2"]["home"] is None  # only 2 earlier games
    assert f["3"]["home"][0] == 2.0 and f["4"]["home"][0] == 2.0
    assert f["3"]["away"][0] == -2.0  # B is the mirror image
    assert f["3"]["home"][1] == 10.0  # 30 - 30 minus (20 - 30)


def test_history_resets_each_season():
    games = _games(4) + [_g(10, "A", "B", 1, 0)]
    games[-1].season = 2025
    box = {g.gid: _box(0.0, 2.0) for g in games}
    assert S.features(games, box, 0.1)["10"]["home"] is None


def test_spots_tag_lucky_sides_and_totals():
    g = _g(0, "A", "B", 30, 20)
    cut = {"tov": (-1.0, 1.0), "fin": (-5.0, 5.0), "tot": (-5.0, 5.0)}
    spots, every = S.spots_for(g, {"home": (2.0, 0.0, 6.0), "away": (-2.0, 0.0, 0.0)}, cut)
    names = [s[0] for s in spots]
    assert "lock-price fav ML: lucky turnover side" in names
    assert "dog ML: unlucky turnover side" in names
    assert "dog ML: turnover-unlucky side vs lucky side" in names
    assert not any("finishing" in n for n in names)  # finishing luck in the middle for both
    assert "under: hot points-per-yard matchup" in names and not any(n.startswith("over") for n in names)
    assert len(every) == 4


def test_run_end_to_end_with_luck_control(tmp_path):
    lines = tmp_path / "lines" / "nfl"
    lines.mkdir(parents=True)
    box = tmp_path / "box" / "nfl"
    box.mkdir(parents=True)
    cols = ["game_id", "season", "home_team", "away_team", "home_score", "away_score", "location", "start",
            "home_moneyline", "away_moneyline", "total_line", "over_odds", "under_odds"]
    b = {}
    with gzip.open(lines / "2024.csv.gz", "wt", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for i in range(30):
            season = 2024 if i < 15 else 2025
            w.writerow([str(i), season, "A", "B", 24, 17 + i % 3 * 7, "Home", f"2025-01-{i + 1:02d}T00:00Z",
                        -150, 130, 44, -110, -110])
            b[str(i)] = _box(float(i % 3), 1.0)
    (box / "2025-01.json").write_text(json.dumps(b))
    res = S.run(tmp_path / "lines", tmp_path / "box", sports=("nfl",), random_n=5)
    assert res["control"]["spots"] == 5 and res["rows"]
    assert all(r["study"] == S.STUDY for r in res["rows"])
