"""First half vs full game: when the two markets disagree, which one is right? (2026-11-18)

Idea: the first-half (MLB: first five innings) moneyline and the full-game moneyline price the same
two teams. Normally the first-half price is a fixed squeeze of the full-game one (a half is shorter,
so the favorite wins it less often). When the first-half market rates a side clearly better (or
worse) than its full-game price implies, one of the two markets is off. If the softer first-half
market is the one that's wrong, the full-game side the half market likes less (or the half side
the full market likes more) should beat its price.

Gap: per sport, logit(no-vig first-half win chance) is fit as a + b * logit(no-vig full-game win
chance) on the tuning seasons only (least squares, home view); the gap is the actual first-half
logit minus that fit. Both prices are closing prices, known before kickoff; no score is used.

Spots, from each side's view: the half market likes the side more (top quarter of tuning gaps) or
less (bottom quarter). Tested on +100..+220 full-game dog moneylines, -170..-120 full-game favorites,
full-game spreads, and the side's own first-half moneyline (any price -170..+220; a tied half is a
push) at the real price. Closing lines 2018+: seasons through TUNE_LAST tune (fit and cutoffs),
later ones are blind. Luck control: random tenths of all bets through the same bar.
"""

from __future__ import annotations

import math
import random
from pathlib import Path

from .atsstudy import cover_margin
from .linestudies import SMALL_DOG, TUNE_LAST, G, _dog, _fair, load, verdict
from .nflhist import Record

STUDY = "45 first half vs full game gap"
SPORTS = ("nfl", "ncaaf", "nba", "ncaab", "mlb")  # NHL's archive has almost no first-period lines


def _logit(p: float) -> float:
    p = min(max(p, 1e-4), 1 - 1e-4)
    return math.log(p / (1 - p))


def pairs(games: list[G]) -> list[tuple[float, float] | None]:
    """Each game -> (full-game logit, first-half logit) for the home side, or None without both prices."""
    out = []
    for g in games:
        full, half = _fair(g.ml), _fair(g.h1_ml) if g.h1_ml else None
        out.append((_logit(full["home"]), _logit(half["home"])) if full and half else None)
    return out


def fit(games: list[G], xy: list) -> tuple[float, float] | None:
    """Least squares a + b*x on the tuning seasons only."""
    pts = [p for g, p in zip(games, xy) if p is not None and g.season <= TUNE_LAST]
    if len(pts) < 100:
        return None
    n = len(pts)
    mx = sum(x for x, _ in pts) / n
    my = sum(y for _, y in pts) / n
    sxx = sum((x - mx) ** 2 for x, _ in pts)
    if sxx < 1e-9:  # no spread in the full-game prices: nothing to fit
        return None
    b = sum((x - mx) * (y - my) for x, y in pts) / sxx
    return my - b * mx, b


def gaps(games: list[G], xy: list, ab: tuple[float, float]) -> list[float | None]:
    a, b = ab
    return [None if p is None else p[1] - (a + b * p[0]) for p in xy]


def cutoffs(games: list[G], gs: list) -> tuple[float, float] | None:
    """Bottom and top quarter of the tuning seasons' gaps, both views (home gap and its mirror)."""
    xs = sorted(v for g, x in zip(games, gs) if x is not None and g.season <= TUNE_LAST for v in (x, -x))
    if len(xs) < 80:
        return None
    return xs[len(xs) // 4], xs[(3 * len(xs)) // 4]


def spot(gap: float | None, side: str, cut: tuple[float, float]) -> str | None:
    if gap is None:
        return None
    g = gap if side == "home" else -gap
    if g >= cut[1]:
        return "half market likes it more"
    if g <= cut[0]:
        return "half market likes it less"
    return None


def half_result(g: G, side: str) -> float | None:
    if None in g.h1.values():
        return None
    m = g.h1["home"] - g.h1["away"]
    m = m if side == "home" else -m
    return 1.0 if m > 0 else 0.0 if m < 0 else 0.5


def study(games: list[G], sport: str, recs: dict, control: dict, n_random: int = 100, seed: int = 45) -> None:
    xy = pairs(games)
    ab = fit(games, xy)
    if ab is None:
        return
    gs = gaps(games, xy, ab)
    cut = cutoffs(games, gs)
    if cut is None:
        return
    rng = random.Random(f"{seed}:{sport}")
    chosen = [[rng.random() < 0.1 for _ in games] for _ in range(n_random)]

    def put(book, key, g, price, res):
        arm = "blind" if g.season > TUNE_LAST else "in_sample"
        book.setdefault(key, {"in_sample": Record(), "blind": Record()})[arm].add(price, res)

    for i, g in enumerate(games):
        if gs[i] is None:
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
            hp = g.h1_ml.get(side)
            hr = half_result(g, side)
            if hp is not None and hr is not None and -170 <= hp <= 220:
                kinds.append(("first-half ML", hp, hr))
            if not kinds:
                continue
            for k in range(n_random):
                if chosen[k][i]:
                    for _, price, res in kinds:
                        put(control, f"random {k}", g, price, res)
            s = spot(gs[i], side, cut)
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
