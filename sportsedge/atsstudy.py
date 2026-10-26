"""Cover streaks: does the spread shade a team after a big cover or a big miss? (study of 2026-10-26)

Idea: bettors chase a team that just covered by a mile and fade one that just missed by a mile.
If the line overreacts, the other side (or the same one) pays. Tested on spread bets at the real
spread price, for the bet side's own last game and the opponent's last game, in the four sports
that have a real spread. Closing lines 2018+: seasons through TUNE_LAST tune, later ones are blind.
Luck control: random tenths of all spread bets through the same bar.
Data: data/lines/espn/<sport>/<season>.csv.gz.
"""

from __future__ import annotations

import random
from collections import defaultdict
from pathlib import Path

from .linestudies import SPREAD_SPORTS, TUNE_LAST, G, load, verdict
from .nflhist import Record

STUDY = "23 cover margin last game"
BIG = {"nfl": 10, "ncaaf": 14, "nba": 10, "ncaab": 10}  # points past the spread that count as big


def cover_margin(g: G, side: str) -> float | None:
    """Points the side beat the spread by (negative = missed it)."""
    if g.spread is None:
        return None
    m = g.margin + g.spread if side == "home" else -g.margin - g.spread
    return m


def annotate_cover(games: list[G]) -> list[dict]:
    """Each game's side -> last game's cover margin, known before kickoff (reset each season)."""
    last: dict[str, float | None] = {}
    season = None
    out = []
    for g in games:
        if g.season != season:
            season = g.season
            last.clear()
        out.append({"home": last.get(g.home), "away": last.get(g.away)})
        for side, t in (("home", g.home), ("away", g.away)):
            last[t] = cover_margin(g, side)
    return out


def spots(prior: dict, side: str, sport: str) -> list[str]:
    big = BIG[sport]
    other = "away" if side == "home" else "home"
    out = []
    for who, key in (("own", side), ("opponent", other)):
        v = prior.get(key)
        if v is None:
            continue
        if v >= big:
            out.append(f"{who} last game covered by {big}+")
        elif v <= -big:
            out.append(f"{who} last game missed by {big}+")
    return out


def _result(g: G, side: str) -> float:
    m = cover_margin(g, side)
    return 1.0 if m > 0 else 0.0 if m < 0 else 0.5


def study(games: list[G], sport: str, recs: dict, control: dict, n_random: int = 100, seed: int = 23) -> None:
    rng = random.Random(f"{seed}:{sport}")
    chosen = [[rng.random() < 0.1 for _ in games] for _ in range(n_random)]
    priors = annotate_cover(games)

    def put(book, key, g, side):
        arm = "blind" if g.season > TUNE_LAST else "in_sample"
        book.setdefault(key, {"in_sample": Record(), "blind": Record()})[arm].add(g.spread_odds[side], _result(g, side))

    for i, g in enumerate(games):
        if g.spread is None or not (g.spread_odds["home"] and g.spread_odds["away"]):
            continue
        for side in ("home", "away"):
            for k in range(n_random):
                if chosen[k][i]:
                    put(control, f"random {k}", g, side)
            for s in spots(priors[i], side, sport):
                put(recs, (sport, s), g, side)


def run(lines_dir: Path, sports=SPREAD_SPORTS, random_n: int = 100) -> dict:
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
