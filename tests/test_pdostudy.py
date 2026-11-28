import json

from sportsedge import pdostudy as P
from tests.test_dogstudy import _g


def _box(goals_h, goals_a, shots=30.0, ppg=(0.0, 0.0)):
    return {"home": {"pts": goals_h, "shots": shots, "ppg": ppg[0], "ppo": 3.0, "fow": 30.0},
            "away": {"pts": goals_a, "shots": shots, "ppg": ppg[1], "ppo": 3.0, "fow": 30.0}}


def _games(n, season=2024):
    out = []
    for i in range(n):
        g = _g(i, "A", "B", 3, 2)
        g.sport = "nhl"
        g.season = season
        out.append(g)
    return out


def test_luck_is_goals_above_league_rates():
    f, p = P.luck({"pts": 4.0, "shots": 30.0, "ppg": 2.0, "ppo": 5.0}, 0.1, 0.2)
    assert abs(f - 1.0) < 1e-9 and abs(p - 1.0) < 1e-9


def test_rates_use_tuning_games_only():
    games = _games(2) + _games(1, season=2025)
    games[2].gid = "9"
    box = {"0": _box(3.0, 3.0), "1": _box(3.0, 3.0), "9": _box(30.0, 30.0)}
    sh, pp = P.rates(games, box)
    assert abs(sh - 0.1) < 1e-9  # the 2025 blowout never touches the league rate


def test_load_box_drops_games_without_shots(tmp_path):
    b = {"1": _box(3.0, 2.0), "2": _box(3.0, 2.0, shots=0.0), "3": {"home": {"pts": 1.0}, "away": {}}}
    (tmp_path / "2024-10.json").write_text(json.dumps(b))
    assert set(P.load_box(tmp_path)) == {"1"}


def test_features_use_only_earlier_games_and_need_five():
    games = _games(7)
    box = {g.gid: _box(4.0, 3.0) for g in games}  # A finishes +1 above 0.1/shot, B at the rate (net -1)
    box["6"] = _box(20.0, 0.0)  # game 6's own box score must not leak into game 6
    f = P.features(games, box, 0.1, 0.2)
    assert f["4"]["home"] is None  # only 4 earlier games
    fin, pp, tot = f["5"]["home"]
    assert abs(fin - 1.0) < 1e-9 and abs(pp - (-0.6 + 0.6)) < 1e-9 and abs(tot - 1.0) < 1e-9
    assert f["6"]["home"] == f["5"]["home"]  # game 6 sees only games 0-5
    assert abs(f["5"]["away"][0] + 1.0) < 1e-9  # B is the mirror image


def test_history_resets_each_season():
    games = _games(6)
    g = _g(10, "A", "B", 1, 0)
    g.season = 2025
    games.append(g)
    box = {x.gid: _box(4.0, 3.0) for x in games}
    assert P.features(games, box, 0.1, 0.2)["10"]["home"] is None


def test_spots_tag_unlucky_dog_and_hot_under():
    g = _g(0, "A", "B", 3, 2)
    g.sport = "nhl"
    g.ml = {"home": 130, "away": -150}
    cut = {"finishing": (-0.5, 0.5), "power-play": (-0.2, 0.2), "total": (-1.0, 1.0)}
    spots, every = P.spots_for(g, {"home": (-0.8, 0.0, 0.6), "away": (0.9, 0.0, 0.6)}, cut)
    names = [s[0] for s in spots]
    assert "dog ML: unlucky finishing side" in names
    assert "dog ML: finishing unlucky side vs lucky side" in names
    assert "lock-price fav ML: lucky finishing side" in names
    assert not any("power-play" in n for n in names)  # middle of the pack on power plays
    assert "under: hot-finishing matchup" in names and not any(n.startswith("over") for n in names)
    assert len(every) == 4


def test_run_end_to_end_with_luck_control(tmp_path):
    import csv
    import gzip
    lines = tmp_path / "lines" / "nhl"
    lines.mkdir(parents=True)
    box = tmp_path / "box" / "nhl"
    box.mkdir(parents=True)
    cols = ["game_id", "season", "home_team", "away_team", "home_score", "away_score", "location", "start",
            "home_moneyline", "away_moneyline", "total_line", "over_odds", "under_odds"]
    b = {}
    with gzip.open(lines / "2024.csv.gz", "wt", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for i in range(30):
            season = 2024 if i < 15 else 2025
            w.writerow([str(i), season, "A", "B", 3, 2 + i % 3, "Home", f"2025-01-{i + 1:02d}T00:00Z",
                        -150, 130, 5.5, -110, -110])
            b[str(i)] = _box(4.0 if i % 2 else 1.0, 2.0)
    (box / "2025-01.json").write_text(json.dumps(b))
    res = P.run(tmp_path / "lines", tmp_path / "box", random_n=5)
    assert res["control"]["spots"] == 5 and res["rows"]
    assert all(r["study"] == P.STUDY for r in res["rows"])


def _row(variant, roi, se, verdict="PROVEN"):
    rec = {"bets": 300, "flat_roi": roi, "se": se}
    return {"study": P.STUDY, "sport": "nhl", "variant": variant, "verdict": verdict, "in_sample": rec, "blind": rec}


def test_watch_needs_the_luck_guard_before_it_is_a_candidate():
    from sportsedge.proven import MIN_Z

    res = {"rows": [_row(P.WATCH[1], 0.0866, 0.0685)], "control": {"spots": 1, "proven": 0}}
    w = P.watch(res)
    assert w["blind_z"] < MIN_Z and not w["clears_luck_guard"]
    res = {"rows": [_row(P.WATCH[1], 0.20, 0.10)], "control": {"spots": 1, "proven": 0}}
    assert P.watch(res)["clears_luck_guard"]
    res = {"rows": [_row(P.WATCH[1], 0.20, 0.10, verdict="")], "control": {"spots": 1, "proven": 0}}
    assert not P.watch(res)["clears_luck_guard"]  # a high z alone isn't enough: it must pass the bar too
    assert P.watch({"rows": [], "control": {}}) is None
    res["watch"] = P.watch(res)
    assert "Watch:" in P.report(res)


def test_pdo_rows_never_reach_the_live_board(tmp_path):
    """The study writes its own file: the proven-spot reader never sees it, so no units."""
    from sportsedge import proven

    (tmp_path / "pdo_study.json").write_text(json.dumps({"rows": [_row(P.WATCH[1], 0.5, 0.1, "STRONG")]}))
    assert proven.passed(tmp_path) == []


def test_cli_writes_pdo_study(tmp_path, monkeypatch):
    from sportsedge import cli

    fake = {"rows": [_row(P.WATCH[1], 0.08, 0.07)], "control": {"spots": 5, "proven": 0, "strong": 0}}
    monkeypatch.setattr(P, "run", lambda *a, **k: fake)
    assert cli.main(["pdo-study", "--data-dir", str(tmp_path)]) == 0
    out = json.loads((tmp_path / "pdo_study.json").read_text())
    assert out["watch"]["variant"] == P.WATCH[1] and out["watch"]["clears_luck_guard"] is False
