"""Market overreaction: does a team's price jump off its own market history, and is that jump wrong? (2026-11-17)

Idea: the betting market keeps its own running rating of every team, and the closing lines show it.
When today's closing price on a team sits far from what the market's own recent prices imply for
this matchup, the market has "jumped" on that team (mostly after one result or a news item). If the
market overreacts to one game, the side it jumped against should win more than its new price says.

Market rating: each team gets a rating in log-odds units, learned only from earlier closing
moneylines (no-vig): for each game the prior read is r_home - r_away + home edge (0 at a neutral
site); the closing no-vig log-odds z is compared with it, the difference is the jump, and only
then are the ratings moved toward z (rate K per sport). Ratings shrink toward 0 between seasons.
The jump is known before kickoff: it uses closing prices only, never a score.

Spots, from each side's view (needs MIN_GAMES of the side's and the opponent's games this season):
market jumped toward the side (top quarter of the tuning seasons' jumps), jumped against it (bottom
quarter), and those two split by the side's last result (jumped against after a loss = overreaction
to a loss; jumped toward after a win). Tested on +100..+220 moneyline dogs, -170..-120 moneyline
favorites and spread (run/puck line) bets at the real price. Closing lines 2018+: seasons through
TUNE_LAST tune (and set the cutoffs), later ones are blind. Luck control: random tenths of all bets
through the same bar.
"""

from __future__ import annotations

import math
import random
from pathlib import Path

from .atsstudy import cover_margin
from .linestudies import SMALL_DOG, TUNE_LAST, G, _dog, _fair, load, verdict
from .nflhist import Record

STUDY = "44 market overreaction"
SPORTS = ("nfl", "ncaaf", "nba", "ncaab", "mlb", "nhl")
K = {"nfl": 0.25, "ncaaf": 0.25, "nba": 0.1, "ncaab": 0.12, "mlb": 0.05, "nhl": 0.08}
MIN_GAMES = {"nfl": 3, "ncaaf": 3, "nba": 8, "ncaab": 6, "mlb": 15, "nhl": 8}
SHRINK = 0.6  # ratings kept between seasons
HFA_K = 0.01


def _logit(p: float) -> float:
    p = min(max(p, 1e-4), 1 - 1e-4)
    return math.log(p / (1 - p))


def jumps(games: list[G], sport: str) -> list[dict]:
    """Each game -> {"home": jump toward home or None, "away": ..., "last": {side: last result}}.

    A jump of +0.4 for "home" means the closing price rates the home side 0.4 log-odds better than
    the market's own earlier prices said. Uses earlier games' closing prices and results only.
    """
    k = K[sport]
    rating: dict[str, float] = {}
    played: dict[str, int] = {}
    last: dict[str, float] = {}
    hfa = 0.0
    season = None
    out = []
    for g in games:
        if g.season != season:
            season = g.season
            rating = {t: r * SHRINK for t, r in rating.items()}
            played.clear()
            last.clear()
        fair = _fair(g.ml)
        row: dict = {"home": None, "away": None, "last": {"home": last.get(g.home), "away": last.get(g.away)}}
        if fair is not None:
            prior = rating.get(g.home, 0.0) - rating.get(g.away, 0.0) + (0.0 if g.neutral else hfa)
            z = _logit(fair["home"])
            jump = z - prior
            need = MIN_GAMES[sport]
            if played.get(g.home, 0) >= need and played.get(g.away, 0) >= need:
                row["home"], row["away"] = jump, -jump
            rating[g.home] = rating.get(g.home, 0.0) + k * jump
            rating[g.away] = rating.get(g.away, 0.0) - k * jump
            if not g.neutral:
                hfa += HFA_K * jump
        out.append(row)
        for side, team in (("home", g.home), ("away", g.away)):
            played[team] = played.get(team, 0) + 1
            last[team] = g.won(side)
    return out


def cutoffs(games: list[G], js: list[dict]) -> tuple[float, float] | None:
    """(bottom quarter, top quarter) of the tuning seasons' jumps (the home view; the away view mirrors it)."""
    xs = sorted(row["home"] for g, row in zip(games, js) if g.season <= TUNE_LAST and row["home"] is not None)
    if len(xs) < 40:
        return None
    return xs[len(xs) // 4], xs[(3 * len(xs)) // 4]


def spots(row: dict, side: str, cut: tuple[float, float]) -> list[str]:
    lo, hi = cut
    j = row.get(side)
    if j is None:
        return []
    prev = row["last"].get(side)
    out = []
    if j >= hi:
        out.append("market jumped toward")
        if prev == 1.0:
            out.append("jumped toward after a win")
    elif j <= lo:
        out.append("market jumped against")
        if prev == 0.0:
            out.append("jumped against after a loss")
    return out


def study(games: list[G], sport: str, recs: dict, control: dict, n_random: int = 100, seed: int = 44) -> None:
    js = jumps(games, sport)
    cut = cutoffs(games, js)
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
            for s in spots(js[i], side, cut):
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
