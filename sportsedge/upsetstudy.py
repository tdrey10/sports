"""Upset hangover: is a side mispriced right after an upset? (study of 2026-11-12)

Idea: a team that just lost as a big favorite (a shock loss) or won as a big underdog (a shock win)
may get over-corrected by the market in its next game: the shock loser priced too low, the shock
winner too high. The test is the side's own last game and the opponent's, at that game's closing
moneyline. Big favorite / big dog: -200 / +180 (NFL, NCAAF, NBA, NCAAB), -170 / +150 (MLB, NHL,
whose prices run tighter). Tested for +100..+220 moneyline dogs, -170..-120 moneyline favorites and
spread (run/puck line) bets at the real price. Earlier games only, resets each season. Closing
lines 2018+: seasons through TUNE_LAST tune, later ones are blind. Luck control: random tenths of
all bets through the same bar. Data: data/lines/espn/<sport>/<season>.csv.gz.
"""

from __future__ import annotations

import random
from pathlib import Path

from .atsstudy import cover_margin
from .linestudies import SMALL_DOG, TUNE_LAST, G, _dog, load, verdict
from .nflhist import Record

STUDY = "40 upset hangover"
SPORTS = ("nfl", "ncaaf", "nba", "ncaab", "mlb", "nhl")
# (big favorite at or shorter than, big dog at or longer than)
BIG = {"nfl": (-200, 180), "ncaaf": (-200, 180), "nba": (-200, 180), "ncaab": (-200, 180),
       "mlb": (-170, 150), "nhl": (-170, 150)}


def last_upset(games: list[G], sport: str) -> list[dict]:
    """Each game's side -> 'shock loss' (lost its previous game this season as a big favorite),
    'shock win' (won it as a big dog), 'other', or None with no earlier priced game this season."""
    fav, dog = BIG[sport]
    last: dict[str, str] = {}
    season = None
    out = []
    for g in games:
        if g.season != season:
            season = g.season
            last.clear()
        out.append({"home": last.get(g.home), "away": last.get(g.away)})
        for side in ("home", "away"):
            team = g.home if side == "home" else g.away
            p = g.ml.get(side)
            if p is None:
                last.pop(team, None)  # no price: we can't say what that game was
                continue
            won = g.won(side)
            if p <= fav and won == 0.0:
                last[team] = "shock loss"
            elif p >= dog and won == 1.0:
                last[team] = "shock win"
            else:
                last[team] = "other"
    return out


def current(games: list[G], sport: str, now, max_days: float = 8) -> dict[str, str]:
    """Team -> 'shock loss' / 'shock win' for today's board: how each team's latest game this season
    went (same rule as last_upset). A team whose latest game we hold is more than max_days old is left
    out: a game missing from our data would make us read the wrong one."""
    fav, dog = BIG[sport]
    last: dict[str, tuple] = {}
    season = None
    for g in games:
        if g.season != season:
            season = g.season
            last.clear()
        for side in ("home", "away"):
            team = g.home if side == "home" else g.away
            p = g.ml.get(side)
            won = g.won(side)
            if p is None:
                kind = None
            elif p <= fav and won == 0.0:
                kind = "shock loss"
            elif p >= dog and won == 1.0:
                kind = "shock win"
            else:
                kind = "other"
            last[team] = (kind, g.start)
    return {t: k for t, (k, when) in last.items()
            if k in ("shock loss", "shock win") and when <= now and (now - when).total_seconds() <= max_days * 86400}


def spots(prior: dict, side: str) -> list[str]:
    other = "away" if side == "home" else "home"
    out = []
    for who, key in (("own", side), ("opponent", other)):
        x = prior.get(key)
        if x in ("shock loss", "shock win"):
            out.append(f"{who} last game was a {x}")
    return out


def study(games: list[G], sport: str, recs: dict, control: dict, n_random: int = 100, seed: int = 40) -> None:
    priors = last_upset(games, sport)
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
