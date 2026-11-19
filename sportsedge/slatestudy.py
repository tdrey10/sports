"""Slate size: are lines softer on busy days, when books price a pile of games at once? (2026-11-19)

Idea: on a day with a huge board (college hoops Saturdays, 15-game NBA/MLB nights) the books and
the sharp money spread their attention over many games; on a quiet day every game gets looked at
hard. If attention matters, the busy-day prices should be the soft ones (or the quiet-day ones,
if a lone game draws all the public money).

Slate: the number of games in the same sport on the same Eastern calendar day (start minus 5h).
The schedule is known in advance, so nothing from the game itself leaks in. Busy = top quarter of
the tuning seasons' slate sizes (per game), quiet = bottom quarter, per sport.

Bets, each side at the real closing price: +100..+220 dog moneyline, -170..-120 favorite
moneyline, spread dog and spread favorite (football and hoops only), over and under. Closing
lines 2018+: seasons through TUNE_LAST tune (and set the cutoffs), later ones are blind. NFL is
left out (its slate is fixed by the TV windows, tested in the kickoff-slot study).
Luck control: random tenths of all bets through the same bar.
"""

from __future__ import annotations

import random
from collections import Counter
from datetime import timedelta
from pathlib import Path

from .atsstudy import cover_margin
from .linestudies import SMALL_DOG, SPREAD_SPORTS, TUNE_LAST, G, _dog, load, verdict
from .nflhist import Record

STUDY = "46 slate size (busy vs quiet days)"
SPORTS = ("ncaaf", "nba", "ncaab", "mlb", "nhl")


def day(g: G):
    return (g.start - timedelta(hours=5)).date()


def slates(games: list[G]) -> list[int]:
    """Each game -> how many games its sport has on that Eastern day."""
    n = Counter(day(g) for g in games)
    return [n[day(g)] for g in games]


def cutoffs(games: list[G], sizes: list[int]) -> tuple[int, int] | None:
    """Bottom and top quarter of the tuning seasons' slate sizes (one value per game)."""
    xs = sorted(s for g, s in zip(games, sizes) if g.season <= TUNE_LAST)
    if len(xs) < 80:
        return None
    lo, hi = xs[len(xs) // 4], xs[(3 * len(xs)) // 4]
    return None if lo >= hi else (lo, hi)  # no spread in slate sizes: nothing to split


def spot(size: int, cut: tuple[int, int]) -> str | None:
    if size >= cut[1]:
        return "busy day"
    if size <= cut[0]:
        return "quiet day"
    return None


def _res(m: float | None) -> float | None:
    return None if m is None else 1.0 if m > 0 else 0.0 if m < 0 else 0.5


def bets(g: G) -> list[tuple[str, float, float]]:
    out = []
    for side in ("home", "away"):
        p = g.ml.get(side)
        if _dog(p, *SMALL_DOG):
            out.append(("dog ML", p, g.won(side)))
        elif p is not None and -170 <= p <= -120:
            out.append(("lock-price fav ML", p, g.won(side)))
        if g.sport in SPREAD_SPORTS and g.spread and g.spread_odds.get(side):
            line = g.spread if side == "home" else -g.spread
            out.append(("spread dog" if line > 0 else "spread fav", g.spread_odds[side], _res(cover_margin(g, side))))
    if g.total is not None:
        pts = g.hs + g.as_
        for k, r in (("over", _res(pts - g.total)), ("under", _res(g.total - pts))):
            if g.total_odds.get(k):
                out.append((k, g.total_odds[k], r))
    return out


def study(games: list[G], sport: str, recs: dict, control: dict, n_random: int = 100, seed: int = 46) -> None:
    sizes = slates(games)
    cut = cutoffs(games, sizes)
    if cut is None:
        return
    rng = random.Random(f"{seed}:{sport}")
    chosen = [[rng.random() < 0.1 for _ in games] for _ in range(n_random)]

    def put(book, key, g, price, res):
        arm = "blind" if g.season > TUNE_LAST else "in_sample"
        book.setdefault(key, {"in_sample": Record(), "blind": Record()})[arm].add(price, res)

    for i, g in enumerate(games):
        kinds = bets(g)
        if not kinds:
            continue
        for k in range(n_random):
            if chosen[k][i]:
                for _, price, res in kinds:
                    put(control, (sport, k), g, price, res)
        s = spot(sizes[i], cut)
        if s:
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
