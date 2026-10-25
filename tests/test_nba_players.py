"""NBA player report (owner, Oct 25): positions, health, form, who's due."""

import json
from datetime import datetime, timedelta, timezone

from sportsedge import nbaplayers, nbastudy

LABELS = ["MIN", "PTS", "FG", "3PT", "FT", "REB", "AST", "TO", "STL", "BLK", "OREB", "DREB", "PF", "+/-"]


def ath(name, pos, starter, mins, pts, fg, tp, ft, reb=5, ast=3, pm="+4", dnp=False):
    return {"athlete": {"displayName": name, "position": {"abbreviation": pos}}, "starter": starter,
            "didNotPlay": dnp, "stats": [] if dnp else [str(mins), str(pts), fg, tp, ft, str(reb), str(ast), "2", "1",
                                                          "0", "1", "4", "2", pm]}


def summary(home_rows, away_rows):
    blk = lambda team, rows: {"team": {"displayName": team}, "statistics": [{"labels": LABELS, "athletes": rows}]}  # noqa
    return {"boxscore": {"players": [blk("Away Team", away_rows), blk("Home Team", home_rows)]}}


def test_parse_reads_position_starter_and_made_attempted():
    rows = nbaplayers.parse(summary(
        [ath("Pat Guard", "PG", True, 34, 28, "10-18", "3-7", "5-6", pm="-12"),
         ath("Bench Guy", "SF", False, 0, 0, "0-0", "0-0", "0-0"),
         ath("Hurt Guy", "C", False, 0, 0, "", "", "", dnp=True)],
        [ath("Al Center", "C", True, 30, 12, "5-9", "0-0", "2-4")]))
    by = {r[1]: dict(zip(nbaplayers.COLS, r)) for r in rows}
    assert set(by) == {"Pat Guard", "Al Center"}  # no minutes / did not play: left out
    p = by["Pat Guard"]
    assert (p["pos"], p["starter"], p["min"], p["pts"], p["pm"]) == ("PG", True, 34, 28, -12)
    assert (p["fgm"], p["fga"], p["tpm"], p["tpa"], p["ftm"], p["fta"]) == (10, 18, 3, 7, 5, 6)


def _store(root, season, games):
    p = root / "players" / "nba" / f"{season}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(games))


def test_build_flags_cold_players_holes_and_injury_share(tmp_path):
    now = datetime(2026, 10, 25, 18, 0, tzinfo=timezone.utc)
    row = lambda team, name, pos, st, mins, pts, fgm, fga: [team, name, pos, st, mins, pts, 5, 3, 0, fgm, fga, 1, 4, 2, 2]  # noqa
    last = {f"p{i}": {"date": (now - timedelta(days=300 - i)).isoformat(), "home": "Home Team", "away": "Away Team",
                      "players": [row("Home Team", "Pat Guard", "PG", True, 34, 26, 10, 18),
                                  row("Home Team", "Sub Guard", "PG", False, 20, 8, 3, 7)]} for i in range(30)}
    this = {f"c{i}": {"date": (now - timedelta(days=6 - i)).isoformat(), "home": "Home Team", "away": "Away Team",
                      "players": [row("Home Team", "Pat Guard", "PG", True, 33, 12, 5, 19),
                                  row("Home Team", "Sub Guard", "PG", False, 20, 8, 3, 7)]} for i in range(5)}
    _store(tmp_path, 2025, last)
    _store(tmp_path, 2026, this)
    rep = nbaplayers.build(now, tmp_path, injuries={"Home Team": {"out": [("Pat Guard", "PG")], "questionable": []}},
                           games_today=[{"home": "Home Team", "away": "Away Team", "start": now.isoformat(),
                                         "home_ml": -120, "away_ml": 100, "spread": -2.5}])
    pat = next(p for p in rep["players"] if p["name"] == "Pat Guard")
    assert pat["form"]["tag"] == "cold" and pat["form"]["vs"] == "last season"
    t = rep["teams"]["Home Team"]
    assert t["lineup"]["PG"] == "Pat Guard" and t["holes"][0]["next"] == "Sub Guard"
    assert round(t["minutes_out"], 2) == round(33 / 53, 2)
    notes = " ".join(rep["matchups"][0]["notes"])
    assert "Team without Pat Guard (33 min, 12.0 pts a game)" in notes and "Sub Guard likely starts at PG for Pat Guard" in notes
    assert "Pat Guard (Team) is playing like a bum right now" in notes


def test_luck_is_record_minus_point_differential():
    t = nbastudy.Team()
    for pf, pa in [(100, 99), (101, 100), (99, 120), (98, 118)]:  # 2-2 with a -57 differential
        t.add(pf, pa, False)
    assert 0.25 < t.luck() < 0.35 and t.close == 2  # .500 record, about .21 by point differential
