"""Whole-roster reads shared by the player reports (owner, Oct 25: "don't just look into one player.
Look at the entire roster. Who's important and impactful and who's not?").

- ``margins``: each finished game's margin for both teams, from data/games/<sport>/*.csv.
- ``with_without``: per player, the team's average margin in games he played against games he
  missed (counted only from his first game with that team, so a new arrival isn't "missing" the
  games before he got there). The most direct "how much does this team need him" number there is;
  small samples are labeled, never hidden.
- ``tiers``: Key / Starter / Rotation / Bench by each player's share of what his team produces.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path

MIN_WITHOUT = 2  # games missed before a with/without number is shown


def margins(root, sport: str) -> dict[str, dict[str, float]]:
    """gid -> {team: its margin} for every finished game we hold."""
    out: dict[str, dict[str, float]] = {}
    for f in sorted((Path(root) / "games" / sport).glob("*.csv")):
        with f.open() as fh:
            for r in csv.DictReader(fh):
                try:
                    hs, as_ = float(r["home_score"]), float(r["away_score"])
                except (KeyError, TypeError, ValueError):
                    continue
                out[r["event_id"]] = {r["home"]: hs - as_, r["away"]: as_ - hs}
    return out


def with_without(appear: dict[str, dict[str, set]], dates: dict[str, str], margin: dict[str, dict[str, float]],
                 team: str) -> dict[str, dict]:
    """appear: gid -> {team: names who played}; dates: gid -> ISO date.
    Returns name -> {"with": avg margin, "without": avg margin or None, "n_with", "n_without", "diff"}."""
    games = sorted((dates[g], g) for g, teams in appear.items() if team in teams and g in margin and team in margin[g])
    first: dict[str, int] = {}
    for i, (_, g) in enumerate(games):
        for n in appear[g][team]:
            first.setdefault(n, i)
    out = {}
    for n, i0 in first.items():
        w = [margin[g][team] for _, g in games[i0:] if n in appear[g][team]]
        wo = [margin[g][team] for _, g in games[i0:] if n not in appear[g][team]]
        avg = lambda xs: sum(xs) / len(xs) if xs else None  # noqa: E731
        d = {"with": avg(w), "without": avg(wo), "n_with": len(w), "n_without": len(wo), "diff": None}
        if len(wo) >= MIN_WITHOUT and w:
            d["diff"] = round(d["with"] - d["without"], 1)
        d["with"] = d["with"] if d["with"] is None else round(d["with"], 1)
        d["without"] = d["without"] if d["without"] is None else round(d["without"], 1)
        out[n] = d
    return out


def tiers(values: dict[str, float], key_share: float, starter_share: float, rotation_share: float) -> dict[str, str]:
    """name -> Key / Starter / Rotation / Bench by share of the team's total ``values``."""
    total = sum(v for v in values.values() if v > 0) or 1.0
    out = {}
    for n, v in values.items():
        s = v / total
        out[n] = "Key" if s >= key_share else "Starter" if s >= starter_share else "Rotation" if s >= rotation_share else "Bench"
    return out


def team_index(rows_by_game: dict[str, dict], team_of_row, name_of_row) -> tuple[dict, dict]:
    """(appear, dates) for with_without from a player store: gid -> {"date", "rows" | "players"}."""
    appear: dict[str, dict[str, set]] = defaultdict(lambda: defaultdict(set))
    dates = {}
    for gid, g in rows_by_game.items():
        dates[gid] = g["date"]
        for r in g.get("players") or g.get("rows") or []:
            appear[gid][team_of_row(r)].add(name_of_row(r))
    return appear, dates


def impact_line(ww: dict | None) -> str | None:
    """'team +6.2 pts a game better with him (5 games without)' or None."""
    if not ww or ww.get("diff") is None:
        return None
    d = ww["diff"]
    return f"team {abs(d):.1f} {'better' if d >= 0 else 'worse'} with him ({ww['n_without']} games without)"


__all__ = ["margins", "with_without", "tiers", "team_index", "impact_line", "MIN_WITHOUT"]
