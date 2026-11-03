"""Venue splits: how a team does at this venue type vs overall, tested blind (study of 2026-11-03).

Idea: a team whose average margin at home (or on the road) runs well above its overall margin is
"strong at this venue"; one well below is "weak at it". If the market prices home field the same
for everyone, the strong-at-venue side should beat its price. For each game and each side:
split = (avg margin in this side's games at this venue type) - (avg margin overall), from earlier
games only, reset every season, min MIN_GAMES overall and MIN_SPLIT at the venue type. A side is
"strong" at split >= +EDGE and "weak" at <= -EDGE; also strong-vs-weak matchups.
Bets at the real closing price: moneyline lock-price favorites (-170 to -120), moneyline dogs
(+100 to +220), and the spread in the four spread sports. Closing lines 2018+: seasons through
TUNE_LAST tune, later ones are blind.
Luck control: random tenths of all lock/dog sides through the same bar.
Data: data/lines/espn/<sport>/<season>.csv.gz.
"""

from __future__ import annotations

import random
from pathlib import Path

from .formtrendstudy import DOG, LOCK, cover
from .linestudies import SPORTS, SPREAD_SPORTS, TUNE_LAST, G, load, verdict
from .nflhist import Record

STUDY = "30 venue splits (margin at this venue type minus overall margin)"
MIN_GAMES = {"nfl": 6, "ncaaf": 6, "nba": 14, "ncaab": 12, "nhl": 16, "mlb": 30}
MIN_SPLIT = {"nfl": 3, "ncaaf": 3, "nba": 6, "ncaab": 6, "nhl": 7, "mlb": 12}
EDGE = {"nfl": 4.0, "ncaaf": 5.0, "nba": 3.0, "ncaab": 3.0, "nhl": 0.6, "mlb": 0.6}


def history(games: list[G]) -> list[dict]:
    """Per game, each side's split (None until enough games), from earlier games only."""
    out = []
    season = None
    tot: dict = {}  # team -> {"all": [sum, n], "home": [sum, n], "away": [sum, n]}
    for g in games:
        if g.season != season:
            season = g.season
            tot.clear()

        def split(t, venue):
            d = tot.get(t)
            if not d or d["all"][1] < MIN_GAMES[g.sport] or d[venue][1] < MIN_SPLIT[g.sport]:
                return None
            return d[venue][0] / d[venue][1] - d["all"][0] / d["all"][1]

        out.append({"home": split(g.home, "home"), "away": split(g.away, "away")})
        for t, venue, m in ((g.home, "home", g.hs - g.as_), (g.away, "away", g.as_ - g.hs)):
            d = tot.setdefault(t, {"all": [0.0, 0], "home": [0.0, 0], "away": [0.0, 0]})
            for k in ("all", venue):
                d[k][0] += m
                d[k][1] += 1
    return out


def spots(g: G, f: dict) -> list[tuple[str, str, str]]:
    """(label, side, market) bets: market 'ml' at the closing moneyline or 'spread' at spread odds."""
    out = []
    if f["home"] is None or f["away"] is None:
        return out
    t = EDGE[g.sport]
    for side, other in (("home", "away"), ("away", "home")):
        mine, theirs = f[side], f[other]
        tags = []
        if mine >= t:
            tags.append("strong at the venue")
        if mine <= -t:
            tags.append("weak at the venue")
        if mine >= t and theirs <= -t:
            tags.append("strong vs weak at the venue")
        if mine <= -t and theirs >= t:
            tags.append("weak vs strong at the venue")
        p = g.ml.get(side)
        kind = None if p is None else "lock fav" if LOCK[0] <= p <= LOCK[1] else "dog" if DOG[0] <= p <= DOG[1] else None
        for tag in tags:
            lab = f"{side} " + tag
            if kind:
                out.append((f"{kind}, {lab}", side, "ml"))
            if g.sport in SPREAD_SPORTS and g.spread is not None and g.spread_odds.get(side):
                out.append((f"spread, {lab}", side, "spread"))
    return out


def study(games: list[G], sport: str, recs: dict, control: dict, n_random: int = 100, seed: int = 34) -> None:
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
