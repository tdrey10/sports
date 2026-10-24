"""Teams beating or missing their prices, tested blind (study of 2026-10-24).

Idea: does the market under-react to how a team has done against its own prices this season?
For every team, wins over expectation = results minus the no-vig win chance of each earlier game
this season (per game average). Hot = clearly beating its prices, cold = clearly missing them.
Spots for dogs (+100 to +220 closing) and lock-price favorites (-170 to -120): the bet side hot or
cold, and the opponent hot or cold. Needs MIN_GAMES earlier games; a new season resets.
Closing lines 2018+: seasons through TUNE_LAST tune, later ones are blind.
Luck control: random tenths of all dogs and lock favorites through the same bar.
Data: data/lines/espn/<sport>/<season>.csv.gz.
"""

from __future__ import annotations

import random
from pathlib import Path

from .linestudies import SMALL_DOG, SPORTS, TUNE_LAST, G, _dog, _fair, _other, load, verdict
from .nflhist import Record

STUDY = "23 beating the price"
MIN_GAMES = {"nfl": 5, "ncaaf": 5, "nba": 15, "ncaab": 12, "nhl": 15, "mlb": 30}
HOT = {"nfl": 0.08, "ncaaf": 0.08, "nba": 0.05, "ncaab": 0.05, "nhl": 0.04, "mlb": 0.03}  # per-game avg


def is_lock_price(price) -> bool:
    return price is not None and -170 <= price <= -120


def form_vs_price(games: list[G], sport: str) -> list[dict]:
    """Per game and side: average (result - no-vig win chance) over the team's earlier games this
    season, or None before MIN_GAMES. Only earlier games count; a new season resets."""
    tot: dict[str, float] = {}
    n: dict[str, int] = {}
    out = []
    season = None
    for g in games:
        if g.season != season:
            season = g.season
            tot.clear()
            n.clear()
        row = {}
        for side, team in (("home", g.home), ("away", g.away)):
            row[side] = tot.get(team, 0.0) / n[team] if n.get(team, 0) >= MIN_GAMES[sport] else None
        out.append(row)
        fair = _fair(g.ml)
        if fair:
            for side, team in (("home", g.home), ("away", g.away)):
                tot[team] = tot.get(team, 0.0) + g.won(side) - fair[side]
                n[team] = n.get(team, 0) + 1
    return out


def spots(g: G, side: str, sport: str, form: dict) -> list[str]:
    p = g.ml.get(side)
    kind = "dog" if _dog(p, *SMALL_DOG) else "lock favorite" if is_lock_price(p) else ""
    if not kind:
        return []
    me, opp, t = form[side], form[_other(side)], HOT[sport]
    out = []
    if me is not None:
        if me >= t:
            out.append(f"{kind}: beating its prices this season")
        if me <= -t:
            out.append(f"{kind}: missing its prices this season")
    if opp is not None:
        if opp >= t:
            out.append(f"{kind}: facing a team beating its prices")
        if opp <= -t:
            out.append(f"{kind}: facing a team missing its prices")
    if me is not None and opp is not None:
        if me - opp >= 2 * t:
            out.append(f"{kind}: price-beating gap 2x in our favor")
        if opp - me >= 2 * t:
            out.append(f"{kind}: price-beating gap 2x against us")
    return out


def study(games: list[G], sport: str, recs: dict, control: dict, n_random: int = 100, seed: int = 24) -> None:
    rng = random.Random(f"{seed}:{sport}")
    chosen = [[rng.random() < 0.1 for _ in games] for _ in range(n_random)]
    form = form_vs_price(games, sport)

    def put(book, key, g, side):
        arm = "blind" if g.season > TUNE_LAST else "in_sample"
        book.setdefault(key, {"in_sample": Record(), "blind": Record()})[arm].add(g.ml[side], g.won(side))

    for i, g in enumerate(games):
        for side in ("home", "away"):
            labels = spots(g, side, sport, form[i])
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
