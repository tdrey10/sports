import json

from sportsedge import shootluckstudy as S
from tests.test_dogstudy import _g


def _box(tpm_h, tpm_a):
    s = lambda m: {"tpm": m, "tpa": 30.0, "ftm": 15.0, "fta": 20.0}  # noqa: E731
    return {"home": s(tpm_h), "away": s(tpm_a)}


def _games(n, season=2024):
    out = []
    for i in range(n):
        g = _g(i, "A", "B", 100, 90)
        g.season = season
        out.append(g)
    return out


def test_luck_is_points_above_league_rates():
    assert S.luck({"tpm": 12, "tpa": 30, "ftm": 15, "fta": 20}, 0.3, 0.75) == 3 * 3 + 0
    assert S.luck({"tpm": 9, "tpa": 30, "ftm": 13, "fta": 20}, 0.3, 0.75) == -2


def test_features_use_only_earlier_games_and_need_five():
    games = _games(7)
    box = {g.gid: _box(12.0, 9.0) for g in games}
    box["6"] = _box(30.0, 0.0)  # game 6's own shooting must not leak into game 6
    f = S.features(games, box, 0.3, 0.75)
    assert f["4"]["home"] is None  # only 4 earlier games
    net, tot = f["5"]["home"]
    assert net == 9.0 and tot == 9.0  # A: +9 own, 0 against every earlier game
    assert f["6"]["home"] == (9.0, 9.0)  # game 6 sees only games 0-5
    assert f["5"]["away"][0] == -9.0  # B is the mirror image


def test_history_resets_each_season():
    games = _games(6) + [_g(10, "A", "B", 1, 0)]
    games[-1].season = 2025
    box = {g.gid: _box(12.0, 9.0) for g in games}
    f = S.features(games, box, 0.3, 0.75)
    assert f["10"]["home"] is None


def test_spots_tag_lucky_side_and_totals():
    g = _g(0, "A", "B", 100, 90)
    cut = {"lo": -2.0, "hi": 2.0, "tot_lo": -5.0, "tot_hi": 5.0}
    spots, every = S.spots_for(g, {"home": (3.0, 4.0), "away": (-3.0, 4.0)}, cut)
    names = [s[0] for s in spots]
    assert "lock-price fav ML: lucky shooting side" in names
    assert "dog ML: unlucky shooting side" in names
    assert "dog ML: unlucky side vs lucky side" in names
    assert "under: hot-shooting matchup" in names and not any(n.startswith("over") for n in names)
    assert len(every) == 4  # two sides' moneylines plus over and under


def test_run_end_to_end_with_luck_control(tmp_path):
    import csv
    import gzip
    lines = tmp_path / "lines" / "nba"
    lines.mkdir(parents=True)
    box = tmp_path / "box" / "nba"
    box.mkdir(parents=True)
    cols = ["game_id", "season", "home_team", "away_team", "home_score", "away_score", "location", "start",
            "home_moneyline", "away_moneyline", "total_line", "over_odds", "under_odds"]
    b = {}
    with gzip.open(lines / "2024.csv.gz", "wt", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for i in range(30):
            season = 2024 if i < 15 else 2025
            w.writerow([str(i), season, "A", "B", 100, 90 + i % 3 * 10, "Home", f"2025-01-{i + 1:02d}T00:00Z",
                        -150, 130, 200, -110, -110])
            b[str(i)] = _box(12.0 if i % 2 else 8.0, 10.0)
    (box / "2025-01.json").write_text(json.dumps(b))
    res = S.run(tmp_path / "lines", tmp_path / "box", sports=("nba",), random_n=5)
    assert res["control"]["spots"] == 5 and res["rows"]
    assert all(r["study"] == S.STUDY for r in res["rows"])
