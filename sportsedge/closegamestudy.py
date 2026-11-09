"""Close-game hangover: is a side mispriced right after a one-score game? (study of 2026-11-09)

Idea: a team that just won or lost a nail-biter (NFL/NCAAF/NBA/NCAAB: 3 points or fewer, MLB: 1 run)
may be read as luckier or unluckier than it was. Tested for +100..+220 moneyline dogs, -170..-120
moneyline favorites and spread bets, for the side's own last game and the opponent's.
Uses earlier games only, resets each season. Closing lines 2018+: seasons through TUNE_LAST tune,
later ones are blind. Luck control: random tenths of all bets through the same bar.
Data: data/lines/espn/<sport>/<season>.csv.gz.
"""

from __future__ import annotations

import random
from pathlib import Path

from .atsstudy import cover_margin
from .linestudies import SMALL_DOG, TUNE_LAST, G, _dog, load, verdict
from .nflhist import Record

STUDY = "35 close-game hangover"
SPORTS = ("nfl", "ncaaf", "nba", "ncaab", "mlb")
CLOSE = {"nfl": 3, "ncaaf": 3, "nba": 3, "ncaab": 3, "mlb": 1}


def last_close(games: list[G], sport: str) -> list[dict]:
    """Each game's side -> 'win' / 'loss' if its previous game this season was within CLOSE, 'other' if
    not, None if it has no earlier game this season."""
    lim = CLOSE[sport]
    last: dict[str, str | None] = {}
    season = None
    out = []
    for g in games:
        if g.season != season:
            season = g.season
            last.clear()
        out.append({"home": last.get(g.home), "away": last.get(g.away)})
        m = g.margin
        for side, mm in (("home", m), ("away", -m)):
            team = g.home if side == "home" else g.away
            last[team] = ("win" if mm > 0 else "loss") if 0 < abs(mm) <= lim else "other"
    return out


def spots(prior: dict, side: str) -> list[str]:
    other = "away" if side == "home" else "home"
    out = []
    for who, key in (("own", side), ("opponent", other)):
        x = prior.get(key)
        if x in ("win", "loss"):
            out.append(f"{who} last game was a close {x}")
    return out


def study(games: list[G], sport: str, recs: dict, control: dict, n_random: int = 100, seed: int = 35) -> None:
    priors = last_close(games, sport)
    rng = random.Random(f"{seed}:{sport}")
    chosen = [[rng.random() < 0.1 for _ in games] for _ in range(n_random)]

    def put(book, key, g, price, res):
        arm = "blind" if g.season > TUNE_LAST else "in_sample"
        book.setdefault(key, {"in_sample": Record(), "blind": Record()})[arm].add(price, res)

    for i, g in enumerate(games):
        for side in ("home", "away"):
            kinds = []
            p = g.ml[side]
            if _dog(p, *SMALL_DOG):
                kinds.append(("dog ML", p, g.won(side)))
            elif p is not None and -170 <= p <= -120:
                kinds.append(("lock-price fav ML", p, g.won(side)))
            sp = g.spread_odds[side] if g.spread is not None else None
            if sp:
                m = cover_margin(g, side)
                kinds.append(("spread", sp, 1.0 if m > 0 else 0.0 if m < 0 else 0.5))
            if not kinds:
                continue
            for k in range(n_random):
                if chosen[k][i]:
                    for _, price, res in kinds:
                        put(control, f"random {k}", g, price, res)
            for s in spots(priors[i], side):
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
