"""Closing-line moves toward or away from a side, tested blind (study of 2026-10-19).

Idea: when the moneyline moves between the opener and the close, does the side the money
moved toward win more than its closing price says? Dogs (+100 to +220 closing) whose implied
chance rose 1, 2, 3+ points (steam toward the dog), dogs whose chance fell 1, 2+ points
(drifting out), and favorites (-170 to -110) whose chance rose 2, 3+ points.
Opening lines start 2023 (MLB 2019): seasons through OPEN_TUNE_LAST tune, later ones are blind.
Luck control: random tenths of all +100..+220 dogs through the same bar.
Data: data/lines/espn/<sport>/<season>.csv.gz.
"""

from __future__ import annotations

import random
from pathlib import Path

from .linestudies import OPEN_TUNE_LAST, SMALL_DOG, SPORTS, G, _dog, load, verdict
from .nflhist import Record

STUDY = "18 line moves"
TOWARD_DOG = (0.01, 0.02, 0.03)
AWAY_DOG = (0.01, 0.02)
TOWARD_FAV = (0.02, 0.03)


def implied(price: float | None) -> float | None:
    if price is None or price == 0:
        return None
    return 100 / (price + 100) if price > 0 else -price / (-price + 100)


def move(g: G, side: str) -> float | None:
    """Change in a side's implied chance from the opener to the close (+ = money came in on it)."""
    a, b = implied(g.open_ml.get(side)), implied(g.ml.get(side))
    return None if a is None or b is None else b - a


def spots(g: G, side: str) -> list[str]:
    m = move(g, side)
    price = g.ml.get(side)
    if m is None or price is None:
        return []
    out = []
    if _dog(price, *SMALL_DOG):
        out += [f"dog moved toward by {t:.0%}+" for t in TOWARD_DOG if m >= t]
        out += [f"dog drifted out by {t:.0%}+" for t in AWAY_DOG if m <= -t]
    elif -170 <= price <= -110:
        out += [f"favorite moved toward by {t:.0%}+" for t in TOWARD_FAV if m >= t]
    return out


def study(games: list[G], sport: str, recs: dict, control: dict, n_random: int = 100, seed: int = 19) -> None:
    rng = random.Random(f"{seed}:{sport}")
    chosen = [[rng.random() < 0.1 for _ in games] for _ in range(n_random)]

    def put(book, key, g, side):
        arm = "blind" if g.season > OPEN_TUNE_LAST else "in_sample"
        book.setdefault(key, {"in_sample": Record(), "blind": Record()})[arm].add(g.ml[side], g.won(side))

    for i, g in enumerate(games):
        if not (g.open_ml.get("home") and g.open_ml.get("away")):
            continue
        for side in ("home", "away"):
            if g.ml.get(side) is None:
                continue
            if _dog(g.ml[side], *SMALL_DOG):
                for k in range(n_random):
                    if chosen[k][i]:
                        put(control, f"random {k}", g, side)
            for s in spots(g, side):
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
