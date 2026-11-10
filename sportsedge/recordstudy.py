"""Better-record dog: is a side mispriced when its win-loss record disagrees with the price? (study of 2026-11-10)

Idea: casual money reads the standings, books price strength. A dog whose season record is better than
the favorite's (or a favorite with the worse record) might be priced off the wrong number. Tested for
+100..+220 moneyline dogs, -170..-120 moneyline favorites and spread bets, by the gap between the bet
side's season win % and its opponent's (both sides need MIN_GAMES earlier games this season).
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

STUDY = "36 better-record dog"
SPORTS = ("nfl", "ncaaf", "nba", "ncaab", "nhl", "mlb")
MIN_GAMES = {"nfl": 3, "ncaaf": 3, "nba": 8, "ncaab": 6, "nhl": 8, "mlb": 15}


def season_records(games: list[G], sport: str) -> list[dict]:
    """Each game's side -> its win % this season before the game, or None with fewer than MIN_GAMES
    earlier games. Ties (NFL) count half a win."""
    need = MIN_GAMES[sport]
    rec: dict[str, list[float]] = {}  # team -> [wins, games]
    season = None
    out = []
    for g in games:
        if g.season != season:
            season = g.season
            rec.clear()
        row = {}
        for side, team in (("home", g.home), ("away", g.away)):
            w, n = rec.get(team, (0.0, 0))
            row[side] = w / n if n >= need else None
        out.append(row)
        m = g.margin
        for team, mm in ((g.home, m), (g.away, -m)):
            w, n = rec.get(team, (0.0, 0))
            rec[team] = [w + (1.0 if mm > 0 else 0.5 if mm == 0 else 0.0), n + 1]
    return out


def spots(prior: dict, side: str) -> list[str]:
    """Record-gap spots for the bet side (own win % minus the opponent's)."""
    other = "away" if side == "home" else "home"
    me, op = prior.get(side), prior.get(other)
    if me is None or op is None:
        return []
    gap = me - op
    out = []
    if gap > 0:
        out.append("better record than the opponent")
    if gap >= 0.2:
        out.append("record 20+ pts better")
    if gap <= -0.2:
        out.append("record 20+ pts worse")
    if me > 0.5 > op:
        out.append("winning record vs a losing one")
    if me < 0.5 < op:
        out.append("losing record vs a winning one")
    return out


def study(games: list[G], sport: str, recs: dict, control: dict, n_random: int = 100, seed: int = 36) -> None:
    priors = season_records(games, sport)
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
            sp = g.spread_odds[side] if g.spread is not None and sport not in ("mlb", "nhl") else None
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
