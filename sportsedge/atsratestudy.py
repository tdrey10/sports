"""Season cover rate: does the spread shade teams that have been covering (or missing) all year?
(study of 2026-11-07)

Idea: a team that has covered 60%+ of its games so far this season (or under 40%) may be priced
too high (or too low) because bettors chase records. Tested on spread bets at the real spread
price, following and fading, for the side's own rate, the opponent's rate, and a big gap between
the two rates. Rates use earlier games only, reset each season, min MIN_GAMES decided games each.
Closing lines 2018+: seasons through TUNE_LAST tune, later ones are blind.
Luck control: random tenths of all spread bets through the same bar.
Data: data/lines/espn/<sport>/<season>.csv.gz.
"""

from __future__ import annotations

import random
from pathlib import Path

from .atsstudy import cover_margin
from .linestudies import SPREAD_SPORTS, TUNE_LAST, G, load, verdict
from .nflhist import Record

STUDY = "33 season cover rate"
MIN_GAMES = {"nfl": 6, "ncaaf": 6, "nba": 15, "ncaab": 12}
HI, LO, GAP = 0.60, 0.40, 0.20


def annotate_rate(games: list[G]) -> list[dict]:
    """Each game's side -> season cover rate (covers / decided games) so far, or None if too few."""
    st: dict[str, list[int]] = {}
    season = None
    out = []
    for g in games:
        if g.season != season:
            season = g.season
            st.clear()
        n = MIN_GAMES[g.sport]
        row = {}
        for side, t in (("home", g.home), ("away", g.away)):
            c = st.get(t)
            row[side] = c[0] / (c[0] + c[1]) if c and c[0] + c[1] >= n else None
        out.append(row)
        for side, t in (("home", g.home), ("away", g.away)):
            m = cover_margin(g, side)
            if m is None or m == 0:
                continue
            c = st.setdefault(t, [0, 0])
            c[0 if m > 0 else 1] += 1
    return out


def spots(prior: dict, side: str) -> list[str]:
    other = "away" if side == "home" else "home"
    own, opp = prior.get(side), prior.get(other)
    out = []
    if own is not None:
        if own >= HI:
            out.append("own side covering 60%+ (follow)")
        if own <= LO:
            out.append("own side covering under 40% (fade-the-fade: bet the cold side)")
    if opp is not None:
        if opp >= HI:
            out.append("opponent covering 60%+ (bet against the hot cover side)")
        if opp <= LO:
            out.append("opponent covering under 40% (bet against the cold cover side)")
    if own is not None and opp is not None and own - opp >= GAP:
        out.append("own cover rate 20+ pts above opponent's")
    return out


def _result(g: G, side: str) -> float:
    m = cover_margin(g, side)
    return 1.0 if m > 0 else 0.0 if m < 0 else 0.5


def study(games: list[G], sport: str, recs: dict, control: dict, n_random: int = 100, seed: int = 33) -> None:
    rng = random.Random(f"{seed}:{sport}")
    chosen = [[rng.random() < 0.1 for _ in games] for _ in range(n_random)]
    priors = annotate_rate(games)

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
            for s in spots(priors[i], side):
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
