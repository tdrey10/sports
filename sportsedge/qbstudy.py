"""NFL quarterback changes as betting spots (study of 2026-11-22).

Idea: the NFL model already docks a team that starts someone other than its regular QB, but nobody
had tested whether the MARKET gets QB changes right. Maybe books overreact to a backup starting
(fading the backup too hard leaves value on him), underreact to the regular starter coming back
(after missing games, his team gets priced off the backup's results), or misprice a QB's very first
start. Spots, for the side and against it:
  - new QB: the starter is not the team's regular one (most frequent of its last 8 starts)
  - regular back: the regular starter returns after someone else started the team's last game
  - first start: a QB never seen starting before in the archive (seasons from 2008, so 2006-07
    veterans aren't counted as debuts)
Bets at the real closing price: +100..+220 moneyline dog, -170..-120 moneyline favorite, the
spread side; plus overs and unders when any of these is in the game. Earlier games only. nflverse
closing lines 2006+ (data/lines/nfl): 2006 only warms the QB history; 2007-2017 tune, 2018+ blind.
Luck control: random tenths of all bets through the same bar.
"""

from __future__ import annotations

import random
from collections import defaultdict
from pathlib import Path

from .linestudies import verdict
from .nflhist import Game, Record, _regular_qb, load

STUDY = "51 NFL QB changes"
TUNE_LAST = 2017
WARMUP_LAST = 2006
FIRST_START_FROM = 2008
NEW = "new QB (not the regular starter)"
BACK = "regular QB back after missing the last game"
DEBUT = "QB's first start"


def qb_context(games: list[Game]) -> list[dict]:
    """Each game -> {'home': [conds], 'away': [conds]} from earlier games only."""
    starts: dict[str, list[str]] = defaultdict(list)  # team -> its earlier starters, in order
    seen: set[str] = set()  # every QB that has started a game in the archive
    out = []
    for g in games:
        row = {}
        for side, team, qb in (("home", g.home, g.home_qb), ("away", g.away, g.away_qb)):
            conds: list[str] = []
            hist = starts[team]
            if qb and hist:
                reg = _regular_qb(hist)
                if reg and qb != reg:
                    conds.append(NEW)
                elif reg and qb == reg and hist[-1] and hist[-1] != qb:
                    conds.append(BACK)
            if qb and g.season >= FIRST_START_FROM and qb not in seen:
                conds.append(DEBUT)
            row[side] = conds
        out.append(row)
        if g.done:
            for team, qb in ((g.home, g.home_qb), (g.away, g.away_qb)):
                starts[team].append(qb)
                if qb:
                    seen.add(qb)
    return out


def _res(m: float) -> float:
    return 1.0 if m > 0 else 0.0 if m < 0 else 0.5


def side_bets(g: Game, side: str) -> list[tuple[str, float, float]]:
    """(kind, price, result) for one side of a finished game."""
    ml = g.home_ml if side == "home" else g.away_ml
    m = g.margin if side == "home" else -g.margin
    out = []
    if ml is not None and 100 <= ml <= 220:
        out.append(("dog ML", ml, _res(m)))
    elif ml is not None and -170 <= ml <= -120:
        out.append(("lock-price fav ML", ml, _res(m)))
    odds = g.home_spread_odds if side == "home" else g.away_spread_odds
    if g.spread is not None and odds:
        # nflverse spread_line: home favored by this many. Home covers when margin > spread.
        cover = (g.margin - g.spread) if side == "home" else (g.spread - g.margin)
        out.append(("spread", odds, _res(cover)))
    return out


def total_bets(g: Game) -> list[tuple[str, float, float]]:
    if g.total is None:
        return []
    pts = g.home_score + g.away_score
    out = []
    if g.over_odds:
        out.append(("over", g.over_odds, _res(pts - g.total)))
    if g.under_odds:
        out.append(("under", g.under_odds, _res(g.total - pts)))
    return out


def study(games: list[Game], recs: dict, control: dict, n_random: int = 100, seed: int = 51) -> None:
    ctxs = qb_context(games)
    rng = random.Random(f"{seed}:nfl")

    def put(book, key, g, price, res):
        arm = "blind" if g.season > TUNE_LAST else "in_sample"
        book.setdefault(key, {"in_sample": Record(), "blind": Record()})[arm].add(price, res)

    for g, ctx in zip(games, ctxs):
        picks = [rng.random() < 0.1 for _ in range(n_random)]  # drawn for every game: stable control
        if not g.done or g.season <= WARMUP_LAST:
            continue
        everything = []
        for side in ("home", "away"):
            other = "away" if side == "home" else "home"
            kinds = side_bets(g, side)
            everything += kinds
            labels = [f"own {c}" for c in ctx[side]] + [f"opponent {c}" for c in ctx[other]]
            for lab in labels:
                for kind, price, res in kinds:
                    put(recs, f"{kind}: {lab}", g, price, res)
        tots = total_bets(g)
        everything += tots
        for c in sorted(set(ctx["home"]) | set(ctx["away"])):
            for kind, price, res in tots:
                put(recs, f"{kind}: game with a {c}", g, price, res)
        for k in range(n_random):
            if picks[k]:
                for _, price, res in everything:
                    put(control, k, g, price, res)


def run(folder: Path, random_n: int = 100) -> dict:
    folder = Path(folder)
    if not (any(folder.glob("*.csv")) or any(folder.glob("*.csv.gz"))):
        return {"rows": [], "control": {"spots": 0, "proven": 0, "strong": 0}}
    recs: dict = {}
    control: dict = {}
    study(load(folder), recs, control, random_n)
    rows = [{"study": STUDY, "sport": "nfl", "variant": k, "verdict": verdict(r, k),
             **{a: r[a].to_dict() for a in r}} for k, r in sorted(recs.items())]
    fakes = [verdict(r) for r in control.values()]
    return {"rows": rows, "control": {"spots": len(fakes), "proven": sum(1 for x in fakes if x),
                                      "strong": fakes.count("STRONG")}}
