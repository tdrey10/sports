"""Series spots: the same two teams playing again right away (study of 2026-11-15).

Idea: in a series (MLB's 2-4 game sets, NBA/NHL/college hoops rematches a day or two apart) the
market may lean on the last game's result. A dog that just lost to this opponent (or is down 0-2 and
facing a sweep) might win more than its price says, or the team that just won might carry over.
A game is a SERIES game when the side's previous game this season was against the same opponent
within GAP_DAYS days. Everything is read from games before the bet.

Spots, per bet side (own = this side, opp = the other side in the same spot):
  won last          - this side won the previous game of the series
  lost last         - this side lost it
  lost last big     - lost it by BIG[sport]+ (blowout in the same matchup)
  facing sweep      - game 3+ of the series and this side lost every earlier game of it
  going for sweep   - game 3+ and this side won every earlier game of it
Tested on +100..+220 moneyline dogs, -170..-120 moneyline favorites and spread (run/puck line) bets at
the real closing price. Closing lines 2018+: seasons through TUNE_LAST tune, later ones are blind.
Luck control: random tenths of all bets through the same bar.
"""

from __future__ import annotations

import random
from pathlib import Path

from .atsstudy import cover_margin
from .linestudies import SMALL_DOG, TUNE_LAST, G, _dog, load, verdict
from .nflhist import Record

STUDY = "43 series spots"
SPORTS = ("mlb", "nhl", "nba", "ncaab")
GAP_DAYS = {"mlb": 2, "nhl": 4, "nba": 4, "ncaab": 4}
BIG = {"mlb": 5, "nhl": 3, "nba": 15, "ncaab": 15}


def series_state(games: list[G], sport: str) -> list[dict]:
    """Each game -> {side: list of this side's results (+margin) in earlier games of the current series}.

    Empty list = not a series game (previous game was another opponent, too long ago, or none).
    """
    gap = GAP_DAYS[sport]
    last: dict[str, tuple] = {}  # team -> (season, start, opponent, [margins of the run vs that opponent])
    out = []
    for g in games:
        row = {}
        for side, team, opp in (("home", g.home, g.away), ("away", g.away, g.home)):
            prev = last.get(team)
            if prev and prev[0] == g.season and prev[2] == opp and (g.start - prev[1]).total_seconds() <= gap * 86400:
                row[side] = list(prev[3])
            else:
                row[side] = []
        out.append(row)
        for side, team, opp in (("home", g.home, g.away), ("away", g.away, g.home)):
            m = (g.hs - g.as_) if side == "home" else (g.as_ - g.hs)
            run = row[side] + [m]
            last[team] = (g.season, g.start, opp, run)
    return out


def side_flags(run: list[float], sport: str) -> dict[str, bool]:
    if not run:
        return {}
    lastm = run[-1]
    return {
        "won last": lastm > 0,
        "lost last": lastm < 0,
        "lost last big": lastm <= -BIG[sport],
        "facing sweep": len(run) >= 2 and all(x < 0 for x in run),
        "going for sweep": len(run) >= 2 and all(x > 0 for x in run),
    }


def spots(state: dict, side: str, sport: str) -> list[str]:
    other = "away" if side == "home" else "home"
    own, opp = side_flags(state.get(side, []), sport), side_flags(state.get(other, []), sport)
    out = []
    for k in ("won last", "lost last", "lost last big", "facing sweep", "going for sweep"):
        if own.get(k):
            out.append(f"own {k}")
        if opp.get(k):
            out.append(f"opp {k}")
    return out


def study(games: list[G], sport: str, recs: dict, control: dict, n_random: int = 100, seed: int = 43) -> None:
    games = sorted(games, key=lambda g: g.start)
    state = series_state(games, sport)
    rng = random.Random(f"{seed}:{sport}")
    chosen = [[rng.random() < 0.1 for _ in games] for _ in range(n_random)]

    def put(book, key, g, price, res):
        arm = "blind" if g.season > TUNE_LAST else "in_sample"
        book.setdefault(key, {"in_sample": Record(), "blind": Record()})[arm].add(price, res)

    for i, g in enumerate(games):
        if g.neutral:
            continue
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
            for s in spots(state[i], side, sport):
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
