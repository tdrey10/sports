"""Margin volatility: do swingy teams' dogs win more than their price says? (study of 2026-11-13)

Idea: an underdog's outright chance grows with the spread of outcomes. A team whose results swing a
lot around what the market expected (big wins and big losses) might be underpriced as a dog, and a
steady team overpriced; for favorites the reverse. Volatility = standard deviation of a side's earlier
results this season versus the market: actual margin minus the closing spread's expected margin in
the NFL, college football, the NBA and college hoops; the raw margin in MLB and the NHL (their
run/puck line is always 1.5, so it says nothing about the expected margin). Needs enough earlier games
(MIN_GAMES). Volatile / steady = top / bottom quarter of the tuning seasons' values per sport (the
cutoffs never see a blind season). Tested on +100..+220 moneyline dogs, -170..-120 moneyline favorites
and spread (run/puck line) bets at the real price: own volatile, own steady, opponent volatile,
opponent steady, and own volatile vs a steady opponent. Closing lines 2018+: seasons through TUNE_LAST
tune, later ones are blind. Luck control: random tenths of all bets through the same bar.
"""

from __future__ import annotations

import math
import random
from pathlib import Path

from .atsstudy import cover_margin
from .linestudies import SMALL_DOG, TUNE_LAST, G, _dog, load, verdict
from .nflhist import Record

STUDY = "41 margin volatility"
SPORTS = ("nfl", "ncaaf", "nba", "ncaab", "mlb", "nhl")
MIN_GAMES = {"nfl": 4, "ncaaf": 4, "nba": 10, "nhl": 10, "ncaab": 8, "mlb": 20}
SPREAD_SPORTS = ("nfl", "ncaaf", "nba", "ncaab")


def _result(g: G, side: str, sport: str) -> float | None:
    """This side's margin against what the market expected (or the raw margin without a real spread)."""
    m = g.margin if side == "home" else -g.margin
    if sport in SPREAD_SPORTS:
        if g.spread is None:
            return None
        expected = -g.spread if side == "home" else g.spread  # ESPN: home line, negative = home favored
        return m - expected
    return m


def volatility(games: list[G], sport: str) -> list[dict]:
    """Each game -> {side: standard deviation of the side's earlier results this season, or None}."""
    need = MIN_GAMES[sport]
    hist: dict[str, list[float]] = {}
    season = None
    out = []
    for g in games:
        if g.season != season:
            season = g.season
            hist.clear()
        row = {}
        for side, team in (("home", g.home), ("away", g.away)):
            xs = hist.get(team, [])
            if len(xs) >= need:
                mu = sum(xs) / len(xs)
                row[side] = math.sqrt(sum((x - mu) ** 2 for x in xs) / (len(xs) - 1))
            else:
                row[side] = None
        out.append(row)
        for side, team in (("home", g.home), ("away", g.away)):
            r = _result(g, side, sport)
            if r is not None:
                hist.setdefault(team, []).append(r)
    return out


def cutoffs(games: list[G], vols: list[dict]) -> tuple[float, float] | None:
    """(bottom quarter, top quarter) of the tuning seasons' volatility values."""
    xs = sorted(v for g, row in zip(games, vols) if g.season <= TUNE_LAST for v in row.values() if v is not None)
    if len(xs) < 40:
        return None
    return xs[len(xs) // 4], xs[(3 * len(xs)) // 4]


def spots(row: dict, side: str, cut: tuple[float, float]) -> list[str]:
    lo, hi = cut
    other = "away" if side == "home" else "home"
    own, opp = row.get(side), row.get(other)
    out = []
    if own is not None:
        if own >= hi:
            out.append("own volatile")
        elif own <= lo:
            out.append("own steady")
    if opp is not None:
        if opp >= hi:
            out.append("opponent volatile")
        elif opp <= lo:
            out.append("opponent steady")
    if own is not None and opp is not None and own >= hi and opp <= lo:
        out.append("volatile vs steady")
    return out


def study(games: list[G], sport: str, recs: dict, control: dict, n_random: int = 100, seed: int = 41) -> None:
    vols = volatility(games, sport)
    cut = cutoffs(games, vols)
    if cut is None:
        return
    rng = random.Random(f"{seed}:{sport}")
    chosen = [[rng.random() < 0.1 for _ in games] for _ in range(n_random)]

    def put(book, key, g, price, res):
        arm = "blind" if g.season > TUNE_LAST else "in_sample"
        book.setdefault(key, {"in_sample": Record(), "blind": Record()})[arm].add(price, res)

    for i, g in enumerate(games):
        for side in ("home", "away"):
            kinds = []
            p = g.ml.get(side)
            if _dog(p, *SMALL_DOG):
                kinds.append(("dog ML", p, g.won(side)))
            elif p is not None and -170 <= p <= -120:
                kinds.append(("lock-price fav ML", p, g.won(side)))
            sp = g.spread_odds.get(side) if g.spread is not None else None
            if sp:
                m = cover_margin(g, side)
                kinds.append(("spread", sp, 1.0 if m > 0 else 0.0 if m < 0 else 0.5))
            if not kinds:
                continue
            for k in range(n_random):
                if chosen[k][i]:
                    for _, price, res in kinds:
                        put(control, f"random {k}", g, price, res)
            for s in spots(vols[i], side, cut):
                for kind, price, res in kinds:
                    put(recs, (sport, f"{kind}: {s}"), g, price, res)


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
