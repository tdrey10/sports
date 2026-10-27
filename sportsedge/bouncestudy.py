"""Bounce-back and letdown by the bet side's last game margin, tested blind (study of 2026-10-27).

Idea: dogs (+100 to +220 closing) and lock-price favorites (-170 to -120) coming off a blowout
loss (bounce back) or a blowout win (letdown), and facing a team off one. Margin is the team's
previous game this season, only earlier games count. Blowout = BIG points (sport-sized).
Closing lines 2018+: seasons through TUNE_LAST tune, later ones are blind.
Luck control: random tenths of all dogs and lock favorites through the same bar.
Data: data/lines/espn/<sport>/<season>.csv.gz.
"""

from __future__ import annotations

import random
from pathlib import Path

from .linestudies import SMALL_DOG, SPORTS, TUNE_LAST, G, _dog, _other, load, verdict
from .nflhist import Record

STUDY = "23 bounce-back and letdown off the last margin"
BIG = {"nfl": 17, "ncaaf": 21, "nba": 20, "ncaab": 20, "nhl": 4, "mlb": 7}  # "blowout" in points/goals/runs


def is_lock_price(price) -> bool:
    return price is not None and -170 <= price <= -120


def last_margins(games: list[G]) -> list[dict]:
    """Per game and side: margin of that team's previous game this season (None if none yet)."""
    last: dict[str, float] = {}
    out = []
    season = None
    for g in games:
        if g.season != season:
            season = g.season
            last.clear()
        out.append({"home": last.get(g.home), "away": last.get(g.away)})
        last[g.home] = g.hs - g.as_
        last[g.away] = g.as_ - g.hs
    return out


def spots(g: G, side: str, sport: str, margins: dict) -> list[str]:
    p = g.ml.get(side)
    kind = "dog" if _dog(p, *SMALL_DOG) else "lock favorite" if is_lock_price(p) else ""
    if not kind:
        return []
    me, opp, n = margins[side], margins[_other(side)], BIG[sport]
    out = []
    if me is not None and me <= -n:
        out.append(f"{kind}: off a blowout loss (bounce back)")
    if me is not None and me >= n:
        out.append(f"{kind}: off a blowout win (letdown)")
    if opp is not None and opp <= -n:
        out.append(f"{kind}: facing a team off a blowout loss")
    if opp is not None and opp >= n:
        out.append(f"{kind}: facing a team off a blowout win")
    return out


def study(games: list[G], sport: str, recs: dict, control: dict, n_random: int = 100, seed: int = 27) -> None:
    rng = random.Random(f"{seed}:{sport}")
    chosen = [[rng.random() < 0.1 for _ in games] for _ in range(n_random)]
    runs = last_margins(games)

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
