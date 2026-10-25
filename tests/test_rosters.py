"""Whole rosters (owner, Oct 25): who matters, with/without, and the NHL report."""

import json
from datetime import datetime, timedelta, timezone

from sportsedge import nhlplayers, roster

NOW = datetime(2026, 10, 25, 15, 0, tzinfo=timezone.utc)


def test_with_without_counts_only_games_after_he_joined():
    margin = {f"g{i}": {"A": m, "B": -m} for i, m in enumerate([10, 8, -6, -4, 12, 9])}
    dates = {f"g{i}": f"2026-10-0{i + 1}" for i in range(6)}
    appear = {f"g{i}": {"A": {"Star"} | ({"New Guy"} if i >= 2 else set()), "B": {"Other"}} for i in range(6)}
    for g in ("g2", "g3"):
        appear[g]["A"] = {"New Guy"}  # the star missed games 3 and 4: the team lost both
    ww = roster.with_without(appear, dates, margin, "A")
    assert ww["Star"]["n_without"] == 2 and ww["Star"]["diff"] == round((10 + 8 + 12 + 9) / 4 - (-6 - 4) / 2, 1)
    assert ww["New Guy"]["n_without"] == 0 and ww["New Guy"]["diff"] is None  # joined at game 3, never missed one
    assert "better with him (2 games without)" in roster.impact_line(ww["Star"])


def test_tiers_by_share_of_production():
    t = roster.tiers({"a": 50, "b": 25, "c": 15, "d": 6, "e": 4}, 0.30, 0.12, 0.05)
    assert t == {"a": "Key", "b": "Starter", "c": "Starter", "d": "Rotation", "e": "Bench"}


def summary(goalie_sv="28", goalie_sa="30"):
    sk = ["BS", "HT", "TK", "+/-", "TOI", "PPTOI", "SHTOI", "ESTOI", "SHFT", "G", "YTDG", "A", "S", "SM", "FW", "FL", "FO%", "GV", "PN", "PIM"]
    gl = ["GA", "SA", "SOS", "SV", "SV%", "ESSV", "PPSV", "SHSV", "TOI", "PIM"]
    row = lambda name, g, a, toi: {"athlete": {"displayName": name},  # noqa: E731
                                   "stats": ["1", "2", "0", "+1", toi, "2:00", "0:00", "16:00", "22", g, "3", a, "4", "1", "5", "4", "55", "1", "0", "0"]}
    return {"boxscore": {"players": [{"team": {"displayName": "Team A"}, "statistics": [
        {"name": "forwards", "labels": sk, "athletes": [row("Top Center", "1", "1", "20:30"), row("Fourth Liner", "0", "0", "8:10")]},
        {"name": "defenses", "labels": sk, "athletes": [row("Top D", "0", "1", "24:00")]},
        {"name": "goalies", "labels": gl, "athletes": [{"athlete": {"displayName": "Starter G"},
                                                        "stats": ["2", goalie_sa, "0", goalie_sv, ".933", "20", "6", "2", "60:00", "0"]},
                                                       {"athlete": {"displayName": "Backup G"}, "stats": ["0", "0", "0", "0", "0", "0", "0", "0", "0:00", "0"]}]},
    ]}]}}


def test_nhl_parse_and_roster(tmp_path):
    rows = nhlplayers.parse(summary())
    by = {r[1]: r for r in rows}
    assert by["Top Center"][2] == "forwards" and by["Top Center"][3]["TOI"] == 20.5 and by["Top Center"][3]["G"] == 1
    assert by["Starter G"][2] == "goalies" and by["Starter G"][3]["SV"] == 28
    assert "Backup G" not in by  # no ice time: didn't play
    store = {f"g{i}": {"date": (NOW - timedelta(days=10 - i)).isoformat(), "home": "Team A", "away": "Team B", "rows": rows}
             for i in range(8)}
    p = tmp_path / "players" / "nhl" / "2026.json"
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps(store))
    rep = nhlplayers.build(NOW, tmp_path, injuries={"Team A": {"out": [("Top Center", "C")], "questionable": [("Starter G", "G")]}},
                           games_today=[{"home": "Team A", "away": "Team B", "start": NOW.isoformat(), "home_ml": -130, "away_ml": 110, "spread": None}])
    a = rep["teams"]["Team A"]
    tiers = {r["name"]: r["tier"] for r in a["roster"]}
    assert tiers["Top Center"] == "Key" and a["roster"][-1]["name"] == "Fourth Liner"  # least ice time, no points: last
    assert a["goalies"][0]["name"] == "Starter G" and a["goalies"][0].get("g1") and a["goalies"][0]["starts"] == 8
    notes = " ".join(rep["matchups"][0]["notes"])
    assert "#1 goalie Starter G questionable" in notes and "key Top Center out" in notes
