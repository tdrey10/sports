"""Totals by game script, tested blind (study of 2026-10-29).

Idea: does the posted total misjudge games by their shape? Over/under when the total is high or
low for its sport (against that sport's tuning-season quartiles of posted totals; lines only,
no results) and when the spread is big (blowout expected, starters sit) or tiny (coin flip).
Closing totals 2018+: seasons through TUNE_LAST tune, later ones are blind.
Luck control: random tenths of all totals (over and under) through the same bar.
Data: data/lines/espn/<sport>/<season>.csv.gz.
"""

from __future__ import annotations

import random
from pathlib import Path

from .linestudies import SPORTS, TUNE_LAST, G, load, verdict
from .nflhist import Record
from .reststudy import result

STUDY = "25 totals by game script"
BIG_SPREAD = {"nfl": 10, "ncaaf": 17, "nba": 10, "ncaab": 12}
TINY_SPREAD = 2.0


def quartiles(games: list[G]) -> tuple[float, float] | None:
    """Low and high quartile of posted totals in the tuning seasons (lines only, no results)."""
    t = sorted(g.total for g in games if g.total and g.season <= TUNE_LAST)
    if len(t) < 40:
        return None
    return t[len(t) // 4], t[3 * len(t) // 4]


def spots(g: G, sport: str, q: tuple[float, float] | None) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    if q and g.total:
        if g.total <= q[0]:
            out += [("under: low total for the sport", "under"), ("over: low total for the sport", "over")]
        if g.total >= q[1]:
            out += [("under: high total for the sport", "under"), ("over: high total for the sport", "over")]
    if g.spread is not None and sport in BIG_SPREAD:
        if abs(g.spread) >= BIG_SPREAD[sport]:
            out += [("under: big spread (blowout expected)", "under"), ("over: big spread (blowout expected)", "over")]
        if abs(g.spread) <= TINY_SPREAD:
            out += [("under: pick'em spread", "under"), ("over: pick'em spread", "over")]
    return out


def study(games: list[G], sport: str, recs: dict, control: dict, n_random: int = 100, seed: int = 29) -> None:
    rng = random.Random(f"{seed}:{sport}")
    q = quartiles(games)

    def put(book, key, g, price, res):
        arm = "blind" if g.season > TUNE_LAST else "in_sample"
        book.setdefault(key, {"in_sample": Record(), "blind": Record()})[arm].add(price, res)

    for g in games:
        res = result(g)
        picks = [rng.random() < 0.1 for _ in range(n_random)]
        if res is None:
            continue
        under, over = res
        for k, hit in enumerate(picks):
            if hit:
                side = "under" if rng.random() < 0.5 else "over"
                put(control, f"random {k}", g, g.total_odds[side], under if side == "under" else over)
        for label, side in spots(g, sport, q):
            put(recs, (sport, label), g, g.total_odds[side], under if side == "under" else over)


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
