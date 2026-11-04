"""Strength of schedule, tested blind (study of 2026-11-04).

Idea: a side whose opponents so far were much better than the other side's opponents has a
"battle-tested" record that may be underrated by the price (or the reverse: soft schedule,
inflated team). For each game and side: sos = average of its earlier opponents' season-to-date
average margin (opponents' numbers as of this game), from earlier games only, reset every
season, min MIN_GAMES for both sides. diff = my sos - their sos. "Tougher" at diff >= +EDGE,
"easier" at <= -EDGE.
Bets at the real closing price: moneyline lock-price favorites (-170 to -120), moneyline dogs
(+100 to +220), and the spread in the four spread sports. Closing lines 2018+: seasons through
TUNE_LAST tune, later ones are blind. Luck control: random tenths of all lock/dog sides.
Data: data/lines/espn/<sport>/<season>.csv.gz.
"""

from __future__ import annotations

import random
from pathlib import Path

from .formtrendstudy import DOG, LOCK, cover
from .linestudies import SPORTS, SPREAD_SPORTS, TUNE_LAST, G, load, verdict
from .nflhist import Record

STUDY = "31 strength of schedule (average margin of earlier opponents)"
MIN_GAMES = {"nfl": 6, "ncaaf": 6, "nba": 15, "ncaab": 12, "nhl": 16, "mlb": 30}
EDGE = {"nfl": 2.0, "ncaaf": 4.0, "nba": 1.5, "ncaab": 2.5, "nhl": 0.2, "mlb": 0.3}


def history(games: list[G]) -> list[dict]:
    """Per game, each side's schedule strength (None until enough games), earlier games only."""
    out = []
    season = None
    marg: dict = {}  # team -> [sum, n]
    opps: dict = {}  # team -> list of opponents faced

    def avg(t):
        s, n = marg[t]
        return s / n

    def sos(t):
        if t not in marg or marg[t][1] < MIN_GAMES[g.sport]:
            return None
        faced = [o for o in opps[t] if o in marg and marg[o][1] > 0]
        return sum(avg(o) for o in faced) / len(faced) if faced else None

    for g in games:
        if g.season != season:
            season = g.season
            marg.clear()
            opps.clear()
        out.append({"home": sos(g.home), "away": sos(g.away)})
        for t, o, m in ((g.home, g.away, g.hs - g.as_), (g.away, g.home, g.as_ - g.hs)):
            d = marg.setdefault(t, [0.0, 0])
            d[0] += m
            d[1] += 1
            opps.setdefault(t, []).append(o)
    return out


def spots(g: G, f: dict) -> list[tuple[str, str, str]]:
    """(label, side, market) bets: market 'ml' at the closing moneyline or 'spread' at spread odds."""
    out = []
    if f["home"] is None or f["away"] is None:
        return out
    t = EDGE[g.sport]
    for side, other in (("home", "away"), ("away", "home")):
        diff = f[side] - f[other]
        tag = "tougher schedule" if diff >= t else "easier schedule" if diff <= -t else None
        if tag is None:
            continue
        p = g.ml.get(side)
        kind = None if p is None else "lock fav" if LOCK[0] <= p <= LOCK[1] else "dog" if DOG[0] <= p <= DOG[1] else None
        lab = f"{side} {tag}"
        if kind:
            out.append((f"{kind}, {lab}", side, "ml"))
        if g.sport in SPREAD_SPORTS and g.spread is not None and g.spread_odds.get(side):
            out.append((f"spread, {lab}", side, "spread"))
    return out


def study(games: list[G], sport: str, recs: dict, control: dict, n_random: int = 100, seed: int = 35) -> None:
    rng = random.Random(f"{seed}:{sport}")
    hist = history(games)

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
        for label, side, market in spots(g, f):
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
