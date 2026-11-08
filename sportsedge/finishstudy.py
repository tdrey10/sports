"""Second-half finish: does the market misread a team that closed its last game strong or faded?
(study of 2026-11-08)

Idea: a team's last game margin after halftime (final margin minus first-half margin) says whether it
rallied or collapsed. Bettors may over- or under-react. Tested for +100..+220 moneyline dogs,
-170..-120 moneyline favorites and spread bets, for the side's own finish and the opponent's.
"Strong" = last game's second-half margin in the top quarter of the tuning seasons for that sport,
"faded" = bottom quarter. Uses earlier games only, resets each season.
Closing lines 2018+: seasons through TUNE_LAST tune, later ones are blind.
Luck control: random tenths of all bets through the same bar.
Data: data/lines/espn/<sport>/<season>.csv.gz (games with first-half scores; no NHL).
"""

from __future__ import annotations

import random
from pathlib import Path

from .atsstudy import cover_margin
from .linestudies import SMALL_DOG, TUNE_LAST, G, _dog, load, verdict
from .nflhist import Record

STUDY = "34 second-half finish"
SPORTS = ("nfl", "ncaaf", "nba", "ncaab", "mlb")
Q = 0.25


def second_half(g: G, side: str) -> float | None:
    h, a = g.h1.get("home"), g.h1.get("away")
    if h is None or a is None:
        return None
    m = (g.margin - (h - a))
    return m if side == "home" else -m


def last_finish(games: list[G]) -> list[dict]:
    """Each game's side -> its previous game's second-half margin this season (None if unknown)."""
    last: dict[str, float | None] = {}
    season = None
    out = []
    for g in games:
        if g.season != season:
            season = g.season
            last.clear()
        out.append({"home": last.get(g.home), "away": last.get(g.away)})
        last[g.home] = second_half(g, "home")
        last[g.away] = second_half(g, "away")
    return out


def cuts(games: list[G], priors: list[dict]) -> tuple[float, float] | None:
    v = sorted(x for g, p in zip(games, priors) if g.season <= TUNE_LAST for x in (p["home"], p["away"]) if x is not None)
    if len(v) < 400:
        return None
    return v[int(len(v) * Q)], v[int(len(v) * (1 - Q)) - 1]


def spots(prior: dict, side: str, lo: float, hi: float) -> list[str]:
    other = "away" if side == "home" else "home"
    out = []
    for who, key in (("own", side), ("opponent", other)):
        x = prior.get(key)
        if x is None:
            continue
        if x >= hi:
            out.append(f"{who} closed last game strong (top-quarter 2nd half)")
        if x <= lo:
            out.append(f"{who} faded last game (bottom-quarter 2nd half)")
    return out


def study(games: list[G], sport: str, recs: dict, control: dict, n_random: int = 100, seed: int = 34) -> None:
    priors = last_finish(games)
    c = cuts(games, priors)
    if c is None:
        return
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
            for s in spots(priors[i], side, *c):
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
