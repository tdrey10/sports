"""Dogs and favorites by point in the season, tested blind (study of 2026-10-22).

Idea: does the price shade early-season games (ratings not settled) or late-season ones
(rotations, tanking, clinched teams)? Tested for dogs (+100 to +220 closing) and lock-price
favorites (-170 to -120) by how many games the team has played and by calendar month.
Closing lines 2018+: seasons through TUNE_LAST tune, later ones are blind.
Luck control: random tenths of all dogs and lock favorites through the same bar.
Data: data/lines/espn/<sport>/<season>.csv.gz.
"""

from __future__ import annotations

import random
from pathlib import Path

from .linestudies import SMALL_DOG, SPORTS, TUNE_LAST, G, _dog, annotate, load, verdict
from .nflhist import Record

STUDY = "21 season timing"
# (label, low, high) on games already played this season by the bet side
PHASES = {
    "nfl": [("first 3 games", 0, 2), ("games 4-9", 3, 8), ("games 10+", 9, 99)],
    "ncaaf": [("first 3 games", 0, 2), ("games 4-8", 3, 7), ("games 9+", 8, 99)],
    "nba": [("first 10 games", 0, 9), ("games 11-40", 10, 39), ("games 41-70", 40, 69), ("games 71+", 70, 99)],
    "ncaab": [("first 8 games", 0, 7), ("games 9-20", 8, 19), ("games 21+", 20, 99)],
    "nhl": [("first 10 games", 0, 9), ("games 11-40", 10, 39), ("games 41-70", 40, 69), ("games 71+", 70, 99)],
    "mlb": [("first 20 games", 0, 19), ("games 21-80", 20, 79), ("games 81-140", 80, 139), ("games 141+", 140, 999)],
}


def is_lock_price(price) -> bool:
    return price is not None and -170 <= price <= -120


def spots(g: G, side: str, sport: str) -> list[str]:
    p = g.ml.get(side)
    kind = "dog" if _dog(p, *SMALL_DOG) else "lock favorite" if is_lock_price(p) else ""
    if not kind:
        return []
    out = [f"{kind}: month {g.start.month:02d}"]
    played = g.ctx[side]["played"]
    for label, lo, hi in PHASES.get(sport, []):
        if lo <= played <= hi:
            out.append(f"{kind}: {label}")
    return out


def study(games: list[G], sport: str, recs: dict, control: dict, n_random: int = 100, seed: int = 22) -> None:
    rng = random.Random(f"{seed}:{sport}")
    chosen = [[rng.random() < 0.1 for _ in games] for _ in range(n_random)]

    def put(book, key, g, side):
        arm = "blind" if g.season > TUNE_LAST else "in_sample"
        book.setdefault(key, {"in_sample": Record(), "blind": Record()})[arm].add(g.ml[side], g.won(side))

    for i, g in enumerate(games):
        for side in ("home", "away"):
            labels = spots(g, side, sport)
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
        games = load(folder, sport)
        annotate(games)
        study(games, sport, recs, control, random_n)
    rows = [{"study": STUDY, "sport": k[0], "variant": k[1], "verdict": verdict(r, k[1]),
             **{a: r[a].to_dict() for a in r}} for k, r in sorted(recs.items())]
    fakes = [verdict(r) for r in control.values()]
    return {"rows": rows, "control": {"spots": len(fakes), "proven": sum(1 for x in fakes if x),
                                      "strong": fakes.count("STRONG")}}
