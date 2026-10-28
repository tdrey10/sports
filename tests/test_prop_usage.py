"""Owner, Oct 28: usage on NBA points props - a teammate's shots go somewhere when he sits."""

import csv
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

from sportsedge import propusage, proven
from tests.test_ledger import make_pick

T0 = datetime(2026, 10, 1, tzinfo=timezone.utc)


def row(name, mins, pts, fga, team="Celtics"):
    return [team, name, "G", True, mins, pts, 0, 0, 0, 0, fga, 0, 0, 0, 0]


def store(n, star_sits_from=None):
    out = {}
    for i in range(n):
        players = [row("Brown", 34, 20, 16), row("Holiday", 30, 10, 8)]
        if star_sits_from is None or i < star_sits_from:
            players.append(row("Tatum", 36, 28, 24))
        out[f"g{i}"] = {"date": (T0 + timedelta(days=i)).isoformat(), "home": "Celtics", "away": "X", "players": players}
    return out


def test_his_projection_takes_the_shots_of_a_teammate_who_sits():
    book = propusage.Book()
    for _, _, teams in sorted(propusage._games_from(store(10))):
        book.add(teams)
    base, gone0 = book.project("Brown", set())
    up, gone = book.project("Brown", {"Tatum"})
    assert base == 20 and gone0 == 0
    assert abs(gone - 24 / 48) < 1e-9 and abs(up - 40) < 1e-9  # Tatum took half the team's shots
    assert ("over", "usage: a rotation teammate out, points 1.5+ over the line") in propusage.variants(up, 25.5, gone)


def test_a_study_with_few_graded_lines_says_collecting(tmp_path):
    (tmp_path / "players" / "nba").mkdir(parents=True)
    (tmp_path / "players" / "nba" / "2026.json").write_text(json.dumps(store(14, star_sits_from=12)))
    (tmp_path / "props_graded" / "nba").mkdir(parents=True)
    with (tmp_path / "props_graded" / "nba" / "2026-10.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["event_id", "start", "player", "market", "close_line", "close_over_price",
                                          "close_under_price", "result"])
        w.writeheader()
        for i in (12, 13):
            w.writerow({"event_id": f"g{i}", "start": (T0 + timedelta(days=i, hours=1)).isoformat(), "player": "Brown",
                        "market": "player_points", "close_line": 22.5, "close_over_price": -110,
                        "close_under_price": -110, "result": "under"})
    res = propusage.run(tmp_path)
    assert res["props"] == 2 and all(r["verdict"] == "collecting" for r in res["rows"])


def test_once_it_passes_a_props_card_posts_with_units(monkeypatch, tmp_path):
    passed = {"market": "over", "variant": "usage: a rotation teammate out, points 1.5+ over the line", "verdict": "PROVEN",
              "study": "NBA usage (points props)", "sport": "nba",
              "blind": {"bets": 90, "flat_roi": 0.1, "se": 0.08}, "in_sample": {"bets": 90, "flat_roi": 0.05}}
    (tmp_path / "prop_usage.json").write_text(json.dumps({"rows": [passed]}))
    book = propusage.Book()
    for _, _, teams in sorted(propusage._games_from(store(10))):
        book.add(teams)
    monkeypatch.setattr(propusage, "current", lambda root="data": book)
    now = datetime(2026, 11, 1, 15, tzinfo=timezone.utc)
    p = make_pick(market="player_points", side="over", point=25.5, american=-115, sport="nba", event_id="e1", player="Brown")
    ctx = NS(home="Celtics", away="X", neutral=False, injuries_unknown=[], injuries={"Celtics": ["Tatum"]}, wind_mph=None,
             event=NS(commence_time=now + timedelta(hours=8)))
    scan = NS(sport=NS(key="nba", name="NBA"), picks=[p], contexts={"e1": ctx})
    [c] = proven.find([scan], now, tmp_path)
    assert c.units == 0.5 and c.pick.player == "Brown" and "projects 40.0 points" in c.headline
    ctx.injuries = {}  # Tatum plays: no teammate out, 20 vs 25.5 is no over
    assert proven.find([scan], now, tmp_path) == []
