"""NBA usage on player points props (owner, Oct 28: "do all three" - pace and usage for props).

A player's points come from his shots, so when teammates who take shots sit, his share goes up. For
every graded points prop (data/props_graded/nba, every line we've logged since Oct 20, 2026):
- his scoring over his last 10 games (data/players/nba box scores, games before this one only);
- each rotation teammate's (20+ minutes a game) share of the team's scoring chances
  (FGA + 0.44 FTA) over the same stretch, and which of them sat this game;
- projection = his scoring / (1 - the share that sat): the missing shots spread in proportion.
The projection against the closing line, at the closing price, by how far off it is, both ways.
Graded lines are new, so the split is by date: the first half tunes, the second half is blind; a
variant needs 60 blind bets before it can pass (same bars as every study). Until then: collecting.

Which teammates sat comes from the box score (who played); on today's board it comes from the injury
report (players ruled out), which is what the bettor knows at the time.
"""

from __future__ import annotations

import csv
import json
from collections import defaultdict, deque
from datetime import datetime
from pathlib import Path

from .linestudies import verdict
from .nflhist import Record

LAST_N = 10
MIN_GAMES = 5
ROTATION_MPG = 20.0
MAX_OUT_SHARE = 0.5
THRESHOLDS = (1.5, 3, 5)
MARKET = "player_points"
COLS = ("team", "name", "pos", "starter", "min", "pts", "reb", "ast", "pm", "fgm", "fga", "tpm", "tpa", "ftm", "fta")


def _games_from(store: dict) -> list[tuple[datetime, str, dict]]:
    out = []
    for gid, g in store.items():
        teams: dict = defaultdict(list)
        for r in g.get("players", []):
            row = dict(zip(COLS, r))
            if (row.get("min") or 0) > 0:
                teams[row["team"]].append(row)
        out.append((datetime.fromisoformat(g["date"]), gid, dict(teams)))
    return out


def _games(root) -> list[tuple[datetime, str, dict]]:
    """(date, game id, {team: [player rows]}) for every NBA box score we hold, oldest first."""
    out = []
    for f in sorted((Path(root) / "players" / "nba").glob("*.json")):
        try:
            out.extend(_games_from(json.loads(f.read_text())))
        except (OSError, ValueError):
            continue
    return sorted(out, key=lambda x: (x[0], x[1]))


def _chances(r: dict) -> float:
    return (r.get("fga") or 0) + 0.44 * (r.get("fta") or 0)


class Book:
    """Each player's and team's last LAST_N games, fed in date order."""

    def __init__(self):
        self.player = defaultdict(lambda: deque(maxlen=LAST_N))  # name -> rows
        self.team = defaultdict(lambda: deque(maxlen=LAST_N))  # team -> [rows of that game]
        self.team_of: dict[str, str] = {}

    def add(self, teams: dict) -> None:
        for team, rows in teams.items():
            self.team[team].append(rows)
            for r in rows:
                self.player[r["name"]].append(r)
                self.team_of[r["name"]] = team

    def rotation_shares(self, team: str) -> dict[str, float]:
        """Rotation player -> his share of the team's scoring chances over its recent games."""
        games = list(self.team[team])
        if not games:
            return {}
        total = sum(_chances(r) for g in games for r in g) / len(games)
        mins, chances = defaultdict(float), defaultdict(float)
        for g in games:
            for r in g:
                mins[r["name"]] += r["min"]
                chances[r["name"]] += _chances(r)
        n = len(games)
        return {p: chances[p] / n / total for p in mins if total and mins[p] / n >= ROTATION_MPG}

    def project(self, name: str, out: set[str]) -> tuple[float, float] | None:
        """(projected points, the shot share that sat), or None without enough games."""
        rows = list(self.player[name])
        if len(rows) < MIN_GAMES:
            return None
        ppg = sum(r["pts"] for r in rows) / len(rows)
        team = self.team_of.get(name)
        shares = self.rotation_shares(team) if team else {}
        gone = min(sum(s for p, s in shares.items() if p in out and p != name), MAX_OUT_SHARE)
        return ppg / (1 - gone), gone


def _graded(root) -> list[dict]:
    rows = []
    for f in sorted((Path(root) / "props_graded" / "nba").glob("*.csv")):
        with f.open() as fh:
            rows.extend(r for r in csv.DictReader(fh) if r.get("market") == MARKET and r.get("result") in ("over", "under"))
    return sorted(rows, key=lambda r: r["start"])


def variants(proj: float, line: float, gone: float) -> list[tuple[str, str]]:
    """(side, variant) for every threshold the projection clears."""
    e = proj - line
    side = "over" if e > 0 else "under"
    out = [(side, f"usage: points {t:g}+ {side} the line") for t in THRESHOLDS if abs(e) >= t]
    if gone > 0 and e >= THRESHOLDS[0]:
        out.append(("over", "usage: a rotation teammate out, points 1.5+ over the line"))
    return out


def run(root="data") -> dict:
    graded = _graded(root)
    games = _games(root)
    by_gid = {gid: (when, teams) for when, gid, teams in games}
    half = graded[len(graded) // 2]["start"] if graded else ""
    book, gi = Book(), 0
    recs: dict = {}
    for r in graded:
        start = datetime.fromisoformat(r["start"])
        while gi < len(games) and games[gi][0] < start:
            book.add(games[gi][2])
            gi += 1
        game = by_gid.get(r["event_id"])
        team = book.team_of.get(r["player"])
        if not game or not team:
            continue
        played = {p["name"] for p in game[1].get(team, [])}
        if r["player"] not in played:
            continue
        out = {p for p in book.rotation_shares(team) if p not in played}
        got = book.project(r["player"], out)
        if got is None:
            continue
        try:
            line = float(r["close_line"])
            price = {"over": float(r["close_over_price"]), "under": float(r["close_under_price"])}
        except (TypeError, ValueError):
            continue
        for side, variant in variants(got[0], line, got[1]):
            arm = "in_sample" if r["start"] < half else "blind"
            recs.setdefault((side, variant), {"in_sample": Record(), "blind": Record()})[arm].add(
                price[side], 1.0 if r["result"] == side else 0.0)
    rows = []
    for (side, variant), rec in sorted(recs.items()):
        v = verdict(rec, variant)
        rows.append({"study": "NBA usage (points props)", "sport": "nba", "market": side, "variant": variant,
                     "verdict": v or ("collecting" if rec["blind"].bets < 60 else "no edge"),
                     **{a: rec[a].to_dict() for a in rec}})
    return {"props": len(graded), "split": half, "rows": rows}


def report(res: dict) -> str:
    lines = [f"NBA usage on points props: {res['props']} graded lines (blind from {res['split'][:10] or '-'})."]
    for row in res["rows"]:
        t, b = row["in_sample"], row["blind"]
        f = lambda x: "  -  " if x["flat_roi"] is None else f"{x['flat_roi']:+.1%}"  # noqa: E731
        lines.append(f"  {row['variant']:<58} tune {t['bets']:>4} {f(t)}  blind {b['bets']:>4} {f(b)} {row['verdict']}")
    return "\n".join(lines)


def current(root="data") -> Book:
    book = Book()
    for _, _, teams in _games(root):
        book.add(teams)
    return book


__all__ = ["Book", "current", "report", "run", "variants"]
