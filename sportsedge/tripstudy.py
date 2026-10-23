"""Road trips and homestands, tested blind (study of 2026-10-23).

Idea: does the price miss teams deep into a road trip or a homestand, or the side facing one?
For every dog (+100 to +220 closing) and lock-price favorite (-170 to -120): how many games in
a row the bet side has already played at its current kind of venue (home or road), whether it
is the first game back after the other kind, and the same for its opponent.
Closing lines 2018+: seasons through TUNE_LAST tune, later ones are blind.
Luck control: random tenths of all dogs and lock favorites through the same bar.
Data: data/lines/espn/<sport>/<season>.csv.gz.
"""

from __future__ import annotations

import random
from pathlib import Path

from .linestudies import SMALL_DOG, SPORTS, TUNE_LAST, G, _dog, _other, load, verdict
from .nflhist import Record

STUDY = "22 road trips and homestands"
LONG = {"nfl": 2, "ncaaf": 2, "nba": 3, "ncaab": 3, "nhl": 3, "mlb": 4}  # "long" run of one venue kind


def is_lock_price(price) -> bool:
    return price is not None and -170 <= price <= -120


def venue_runs(games: list[G]) -> list[dict]:
    """Per game and side: ("home"|"road"|"neutral", games in a row already played at that kind
    before this one, kind of the previous game). Only earlier games count; a new season resets."""
    last: dict[str, tuple] = {}
    out = []
    season = None
    for g in games:
        if g.season != season:
            season = g.season
            last.clear()
        kinds = {"home": "neutral" if g.neutral else "home", "away": "neutral" if g.neutral else "road"}
        row = {}
        for side, team in (("home", g.home), ("away", g.away)):
            kind, run = last.get(team, (None, 0))
            row[side] = {"kind": kinds[side], "run": run if kind == kinds[side] else 0, "prev": kind}
        out.append(row)
        for side, team in (("home", g.home), ("away", g.away)):
            kind, run = last.get(team, (None, 0))
            last[team] = (kinds[side], run + 1 if kind == kinds[side] else 1)
    return out


def spots(g: G, side: str, sport: str, runs: dict) -> list[str]:
    p = g.ml.get(side)
    kind = "dog" if _dog(p, *SMALL_DOG) else "lock favorite" if is_lock_price(p) else ""
    if not kind:
        return []
    me, opp = runs[side], runs[_other(side)]
    n = LONG[sport]
    out = []
    if me["kind"] == "road":
        if me["run"] >= n:
            out.append(f"{kind}: road trip, {n}+ road games already")
        if me["prev"] == "home":
            out.append(f"{kind}: first road game after a home game")
    if me["kind"] == "home":
        if me["run"] >= n:
            out.append(f"{kind}: homestand, {n}+ home games already")
        if me["prev"] == "road":
            out.append(f"{kind}: first home game after a road game")
    if opp["kind"] == "home" and opp["run"] >= n:
        out.append(f"{kind}: facing a team on a {n}+ game homestand")
    if opp["kind"] == "road" and opp["run"] >= n:
        out.append(f"{kind}: facing a team on a {n}+ game road trip")
    return out


def study(games: list[G], sport: str, recs: dict, control: dict, n_random: int = 100, seed: int = 23) -> None:
    rng = random.Random(f"{seed}:{sport}")
    chosen = [[rng.random() < 0.1 for _ in games] for _ in range(n_random)]
    runs = venue_runs(games)

    def put(book, key, g, side):
        arm = "blind" if g.season > TUNE_LAST else "in_sample"
        book.setdefault(key, {"in_sample": Record(), "blind": Record()})[arm].add(g.ml[side], g.won(side))

    for i, g in enumerate(games):
        for side in ("home", "away"):
            labels = spots(g, side, sport, runs[i])
            if not labels:
                continue
            for k in range(n_random):
                if chosen[k][i]:
                    put(control, f"random {k}", g, side)
            for s in labels:
                put(recs, (sport, s), g, side)


def run(lines_dir: Path, sports=SPORTS, random_n: int = 100) -> dict:
    recs: dict = {}
    control: dict = {}
    for sport in sports:
        folder = Path(lines_dir) / sport
        if not any(folder.glob("*.csv.gz")):
            continue
        study(load(folder, sport), sport, recs, control, random_n)
    rows = [{"study": STUDY, "sport": k[0], "variant": k[1], "verdict": verdict(r, k[1]),
             **{a: r[a].to_dict() for a in r}} for k, r in sorted(recs.items())]
    fakes = [verdict(r) for r in control.values()]
    return {"rows": rows, "control": {"spots": len(fakes), "proven": sum(1 for x in fakes if x),
                                      "strong": fakes.count("STRONG")}}
