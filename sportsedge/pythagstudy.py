"""Pythagorean luck: win% vs the win% its points say, tested blind (study of 2026-11-02).

Idea: a team whose record runs well ahead of what its points scored/allowed say ("lucky") should
regress, and one running behind ("unlucky") should bounce. For each team: luck = season win% minus
pythagorean win% (PF^k / (PF^k + PA^k)), from earlier games only, reset every season, min MIN_GAMES
played. A side is "lucky" at luck >= +LUCK and "unlucky" at <= -LUCK; also lucky-vs-unlucky matchups.
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

STUDY = "29 pythagorean luck (win% minus points-implied win%)"
MIN_GAMES = {"nfl": 6, "ncaaf": 6, "nba": 12, "ncaab": 12, "nhl": 15, "mlb": 20}
EXPONENT = {"nfl": 2.37, "ncaaf": 2.37, "nba": 14.0, "ncaab": 10.0, "nhl": 2.15, "mlb": 1.83}
LUCK = {"nfl": 0.12, "ncaaf": 0.12, "nba": 0.08, "ncaab": 0.08, "nhl": 0.07, "mlb": 0.06}


def luck_of(wins: float, games: int, pf: float, pa: float, k: float) -> float | None:
    if games <= 0 or pf + pa <= 0:
        return None
    pyth = pf ** k / (pf ** k + pa ** k)
    return wins / games - pyth


def history(games: list[G]) -> list[dict]:
    """Per game, each side's luck (None until MIN_GAMES), from earlier games only."""
    out = []
    season = None
    tot: dict[str, list[float]] = {}  # team -> [wins, games, pf, pa]
    for g in games:
        if g.season != season:
            season = g.season
            tot.clear()

        def luck(t):
            w, n, pf, pa = tot.get(t, [0, 0, 0.0, 0.0])
            return None if n < MIN_GAMES[g.sport] else luck_of(w, n, pf, pa, EXPONENT[g.sport])

        out.append({"home": luck(g.home), "away": luck(g.away)})
        for t, mine, theirs in ((g.home, g.hs, g.as_), (g.away, g.as_, g.hs)):
            r = tot.setdefault(t, [0.0, 0, 0.0, 0.0])
            r[0] += 1.0 if mine > theirs else 0.5 if mine == theirs else 0.0
            r[1] += 1
            r[2] += mine
            r[3] += theirs
    return out


def spots(g: G, f: dict) -> list[tuple[str, str, str]]:
    """(label, side, market) bets: market 'ml' at the closing moneyline or 'spread' at spread odds."""
    out = []
    if f["home"] is None or f["away"] is None:
        return out
    t = LUCK[g.sport]
    for side, other in (("home", "away"), ("away", "home")):
        mine, theirs = f[side], f[other]
        tags = []
        if mine >= t:
            tags.append("lucky")
        if mine <= -t:
            tags.append("unlucky")
        if mine >= t and theirs <= -t:
            tags.append("lucky vs unlucky")
        if mine <= -t and theirs >= t:
            tags.append("unlucky vs lucky")
        p = g.ml.get(side)
        kind = None if p is None else "lock fav" if LOCK[0] <= p <= LOCK[1] else "dog" if DOG[0] <= p <= DOG[1] else None
        for tag in tags:
            if kind:
                out.append((f"{kind} on a {tag} side", side, "ml"))
            if g.sport in SPREAD_SPORTS and g.spread is not None and g.spread_odds.get(side):
                out.append((f"spread on a {tag} side", side, "spread"))
    return out


def study(games: list[G], sport: str, recs: dict, control: dict, n_random: int = 100, seed: int = 33) -> None:
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
