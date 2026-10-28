"""Totals by schedule fatigue and rest, tested blind (study of 2026-10-28).

Idea: do tired teams (back-to-back, 3rd game in 4 days, short football week) play unders, and
do well-rested teams (long break, bye) play overs, more than the posted total says? Rest comes
from the closing-lines file, games-in-4-days and games played from linestudies.annotate (only
earlier games count). Closing totals 2018+: seasons through TUNE_LAST tune, later ones are blind.
Luck control: random tenths of all totals (over and under) through the same bar.
Data: data/lines/espn/<sport>/<season>.csv.gz.
"""

from __future__ import annotations

import random
from pathlib import Path

from .linestudies import SPORTS, TUNE_LAST, G, annotate, load, verdict
from .nflhist import Record

STUDY = "24 totals by rest and fatigue"
FOOTBALL = ("nfl", "ncaaf")


def result(g: G) -> tuple[float, float] | None:
    """(under, over) result of the game against its posted total, or None if it has no priced total."""
    if not (g.total and g.total_odds.get("under") and g.total_odds.get("over")):
        return None
    pts = g.hs + g.as_
    under = 1.0 if pts < g.total else 0.5 if pts == g.total else 0.0
    return under, 1.0 - under


def spots(g: G, sport: str) -> list[tuple[str, str]]:
    """(label, 'under' or 'over') spots this game fits, from what both teams knew before it."""
    rest = [g.rest.get("home"), g.rest.get("away")]
    out: list[tuple[str, str]] = []
    if None in rest:
        return out
    if sport in FOOTBALL:
        short, long_ = min(rest) <= 5, min(rest) >= 9
        if short:
            out.append(("under: a team on a short week", "under"))
            out.append(("over: a team on a short week", "over"))
        if long_:
            out.append(("under: both off a long break or bye", "under"))
            out.append(("over: both off a long break or bye", "over"))
        return out
    tired, both_tired = min(rest) <= 1, max(rest) <= 1
    if tired:
        out.append(("under: a team on a back-to-back", "under"))
        out.append(("over: a team on a back-to-back", "over"))
    if both_tired:
        out.append(("under: both on a back-to-back", "under"))
        out.append(("over: both on a back-to-back", "over"))
    if min(rest) >= 3:
        out.append(("over: both rested 3+ days", "over"))
        out.append(("under: both rested 3+ days", "under"))
    if max(g.ctx["home"]["in4"], g.ctx["away"]["in4"]) >= 2:
        out.append(("under: a team on its 3rd game in 4 days", "under"))
        out.append(("over: a team on its 3rd game in 4 days", "over"))
    return out


def study(games: list[G], sport: str, recs: dict, control: dict, n_random: int = 100, seed: int = 28) -> None:
    rng = random.Random(f"{seed}:{sport}")
    annotate(games)

    def put(book, key, g, price, res):
        arm = "blind" if g.season > TUNE_LAST else "in_sample"
        book.setdefault(key, {"in_sample": Record(), "blind": Record()})[arm].add(price, res)

    for g in games:
        res = result(g)
        picks = [rng.random() < 0.1 for _ in range(n_random)]  # always drawn: keeps the control stable
        if res is None:
            continue
        under, over = res
        for k, hit in enumerate(picks):
            if hit:
                side = "under" if rng.random() < 0.5 else "over"
                put(control, f"random {k}", g, g.total_odds[side], under if side == "under" else over)
        for label, side in spots(g, sport):
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
