"""Form trend: last-5 margin vs season margin, tested blind (study of 2026-11-01).

Idea: does the market lag a team that is heating up or cooling off? For each team, form = average
margin over its last 5 games minus its average margin over the whole season so far (only earlier
games, min MIN_GAMES played). A side is "hot" when its form is above +HOT-sd and "cold" when below
-HOT-sd (in units of that sport's margin spread), then we bet hot and cold sides: moneyline
lock-price favorites (-170 to -120), moneyline dogs (+100 to +220), and the spread at its posted
price in the four spread sports. Also the sharper cut: the hot side facing a cold side.
Closing lines 2018+: seasons through TUNE_LAST tune, later ones are blind.
Luck control: random tenths of all lock/dog sides through the same bar.
Data: data/lines/espn/<sport>/<season>.csv.gz.
"""

from __future__ import annotations

import random
import statistics
from collections import deque
from pathlib import Path

from .linestudies import SPORTS, SPREAD_SPORTS, TUNE_LAST, G, load, verdict
from .nflhist import Record

STUDY = "28 form trend (last-5 margin vs season margin)"
MIN_GAMES = {"nfl": 6, "ncaaf": 6, "nba": 12, "ncaab": 12, "nhl": 15, "mlb": 20}
LOCK = (-170, -120)
DOG = (100, 220)
HOT = 0.5  # form beyond this many margin-sds counts


def history(games: list[G]) -> list[dict]:
    """Per game, each side's form (last-5 avg margin minus season avg margin; None until
    MIN_GAMES), from earlier games only, reset every season."""
    out = []
    season = None
    marg: dict[str, list[float]] = {}
    for g in games:
        if g.season != season:
            season = g.season
            marg.clear()

        def form(t):
            m = marg.get(t, [])
            if len(m) < MIN_GAMES[g.sport]:
                return None
            return sum(m[-5:]) / 5 - sum(m) / len(m)

        out.append({"home": form(g.home), "away": form(g.away)})
        marg.setdefault(g.home, []).append(g.margin)
        marg.setdefault(g.away, []).append(-g.margin)
    return out


def spots(g: G, f: dict, sd: float) -> list[tuple[str, str, str]]:
    """(label, side, market) bets: market 'ml' at the closing moneyline or 'spread' at spread odds."""
    out = []
    if f["home"] is None or f["away"] is None:
        return out
    for side, other in (("home", "away"), ("away", "home")):
        mine, theirs = f[side] / sd, f[other] / sd
        tags = []
        if mine >= HOT:
            tags.append("hot")
        if mine <= -HOT:
            tags.append("cold")
        if mine >= HOT and theirs <= -HOT:
            tags.append("hot vs cold")
        if mine <= -HOT and theirs >= HOT:
            tags.append("cold vs hot")
        p = g.ml.get(side)
        kind = None if p is None else "lock fav" if LOCK[0] <= p <= LOCK[1] else "dog" if DOG[0] <= p <= DOG[1] else None
        for t in tags:
            if kind:
                out.append((f"{kind} on a {t} side", side, "ml"))
            if g.sport in SPREAD_SPORTS and g.spread is not None and g.spread_odds.get(side):
                out.append((f"spread on a {t} side", side, "spread"))
    return out


def cover(g: G, side: str) -> float:
    """1 / 0 / 0.5 for the side covering its posted spread (g.spread is the home line)."""
    m = g.margin + g.spread if side == "home" else -g.margin - g.spread
    return 1.0 if m > 0 else 0.0 if m < 0 else 0.5


def study(games: list[G], sport: str, recs: dict, control: dict, n_random: int = 100, seed: int = 32) -> None:
    rng = random.Random(f"{seed}:{sport}")
    hist = history(games)
    sd = statistics.pstdev([g.margin for g in games]) / 3 or 1.0  # typical size of a 5-game average swing

    def put(book, key, g, price, res):
        arm = "blind" if g.season > TUNE_LAST else "in_sample"
        book.setdefault(key, {"in_sample": Record(), "blind": Record()})[arm].add(price, res)

    for g, f in zip(games, hist):
        picks = [rng.random() < 0.1 for _ in range(n_random)]
        if g.margin == 0:
            continue
        for k, hit in enumerate(picks):
            if hit:
                side = "home" if rng.random() < 0.5 else "away"
                p = g.ml.get(side)
                if p is not None and (LOCK[0] <= p <= LOCK[1] or DOG[0] <= p <= DOG[1]):
                    put(control, f"random {k}", g, p, g.won(side))
        for label, side, market in spots(g, f, sd):
            if market == "ml":
                put(recs, (sport, label), g, g.ml[side], g.won(side))
            else:
                put(recs, (sport, label), g, g.spread_odds[side], cover(g, side))


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
