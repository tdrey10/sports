"""Football player report (owner, Oct 25): QBs, backs, top targets, injuries, who's due."""

import json
from datetime import datetime, timedelta, timezone

from sportsedge import fbplayers, nbastudy

NOW = datetime(2026, 10, 25, 15, 0, tzinfo=timezone.utc)


def summary():
    grp = lambda name, labels, rows: {"name": name, "labels": labels, "athletes": [  # noqa: E731
        {"athlete": {"displayName": n}, "stats": s} for n, s in rows]}
    return {"boxscore": {"players": [{"team": {"displayName": "Home"}, "statistics": [
        grp("passing", ["C/ATT", "YDS", "AVG", "TD", "INT", "SACKS", "QBR", "RTG"],
            [("Starter QB", ["22/31", "288", "9.3", "2", "1", "1-6", "70", "105"]), ("Backup QB", ["1/1", "4", "4", "0", "0", "0-0", "-", "-"])]),
        grp("rushing", ["CAR", "YDS", "AVG", "TD", "LONG"], [("Lead Back", ["18", "96", "5.3", "1", "22"])]),
        grp("receiving", ["REC", "YDS", "AVG", "TD", "LONG", "TGTS"], [("Top Wideout", ["7", "102", "14.6", "1", "40", "10"])]),
        grp("defensive", ["TOT"], [("Linebacker", ["9"])]),
    ]}]}}


def test_parse_reads_passing_rushing_receiving():
    rows = fbplayers.parse(summary())
    by = {(r[1], r[2]): r[3:] for r in rows}
    assert by[("Starter QB", "passing")] == [22, 31, 288, 2, 1]
    assert by[("Lead Back", "rushing")] == [18, 96, 1, 0, 0]
    assert by[("Linebacker", "defensive")] == [9, 0, 0, 0, 0]  # defense counts now (whole roster)


def _store(root, games):
    p = root / "players" / "nfl" / "2026.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(games))


def test_build_names_the_qb_flags_cold_and_key_injuries(tmp_path):
    games = {}
    for i in range(6):
        cold = i >= 3  # his last 3 games: 4.5 yds/att against 9+ before
        qb = ["Team A", "Joe Starter", "passing", 20, 30, 135 if cold else 280, 0, 1 if cold else 0]
        rb = ["Team A", "Lead Back", "rushing", 15, 70, 0, 0, 0]
        wr = ["Team A", "Big Target", "receiving", 6, 80, 0, 9, 0]
        games[f"g{i}"] = {"date": (NOW - timedelta(days=7 * (6 - i))).isoformat(), "home": "Team A", "away": "Team B",
                          "rows": [qb, rb, wr, ["Team B", "Other QB", "passing", 18, 30, 200, 1, 1]]}
    _store(tmp_path, games)
    rep = fbplayers.build("nfl", NOW, tmp_path,
                          injuries={"Team A": {"out": [("Lead Back", "RB")], "questionable": [("Big Target", "WR")]}},
                          games_today=[{"home": "Team A", "away": "Team B", "start": NOW.isoformat(),
                                        "home_ml": -150, "away_ml": 130, "spread": -3}])
    a = rep["teams"]["Team A"]
    assert a["qb"]["name"] == "Joe Starter" and a["qb"]["tag"] == "cold" and a["qb"]["starts"] == 6
    assert {h["name"]: h["key"] for h in a["hurt"]} == {"Lead Back": "RB1", "Big Target": "top target"}
    notes = " ".join(rep["matchups"][0]["notes"])
    assert "RB1 Lead Back out" in notes and "Joe Starter (Team A) is cold" in notes


def test_due_study_runs_for_football_with_its_own_numbers():
    p = nbastudy.PARAMS["nfl"]
    t = nbastudy.Team(p)
    for pf, pa in [(20, 17), (24, 21), (10, 31), (13, 38)]:  # 2-2, two 3-point wins, two blowouts
        t.add(pf, pa, False)
    assert t.close == 2 and t.luck() >= p.luck  # .500 record, about .25 by point differential
    assert any("one-score" not in k and "close-game" in k for k in nbastudy.rules(p))
