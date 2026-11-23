"""Juice shading: when the book moves the PRICE instead of the line (study of 2026-11-23).

Idea: a spread or total usually sits at -110 both ways. When a book would rather not move off a
key number it shades the juice instead (-120 one way, +100 the other). That shading is the book
telling us where the money went. Is the juiced side (the one the money hit) a winner the market
still under-prices, or is the cheap plus-money side the value? Same question for the size of the
book's cut on a moneyline: is a high-hold market (the book unsure, padding its price) softer for
dogs or favorites than a low-hold one?

Spots, each side at the real closing price:
  - spread side juiced to -120 or worse / spread side at +100 or better (football and hoops)
  - over / under juiced to -120 or worse, and at +100 or better (all six sports; MLB/NHL totals
    sit at 5.5-9.5 and shade the juice all the time, so they have the big samples)
  - moneyline hold (overround) in the top / bottom quarter of the tuning seasons, per sport:
    +100..+220 dog and -170..-120 favorite
Data: ESPN closing lines (data/lines/espn, 2016+; tune through linestudies.TUNE_LAST, blind after)
for college football, NBA, college hoops, MLB, NHL; NFL from nflverse (data/lines/nfl, 2006+:
tune 2006-2017, blind 2018+). Luck control: random tenths of all bets through the same bar.
"""

from __future__ import annotations

import random
from pathlib import Path

from .atsstudy import cover_margin
from .linestudies import SMALL_DOG, SPREAD_SPORTS, TUNE_LAST, _dog, load, verdict
from .nflhist import Record
from .nflhist import load as load_nfl

STUDY = "52 juice shading and market hold"
ESPN_SPORTS = ("ncaaf", "nba", "ncaab", "mlb", "nhl")
NFL_TUNE_LAST = 2017
JUICED = -120  # this price or worse = the book shaded the juice onto this side
PLUS = 100  # this price or better = the cheap side of a shaded market


def implied(p: float) -> float:
    return 100 / (p + 100) if p > 0 else -p / (-p + 100)


def hold(a: float | None, b: float | None) -> float | None:
    """The book's cut on a two-way price (overround), e.g. -110/-110 -> 0.0476."""
    return None if not a or not b else implied(a) + implied(b) - 1


def juice_tag(price: float | None) -> str | None:
    if price is None:
        return None
    if price <= JUICED:
        return "juiced -120 or worse"
    if price >= PLUS:
        return "plus money"
    return None


def _res(m: float | None) -> float | None:
    return None if m is None else 1.0 if m > 0 else 0.0 if m < 0 else 0.5


def rows_for(sport: str, season: int, ml: dict, spread_cover: dict, spread_odds: dict,
             total_res: dict, total_odds: dict, won: dict, hold_cut: tuple | None) -> tuple[list, list]:
    """-> (spot bets [(variant, price, result)], every bet for the luck control [(price, result)])."""
    spots, every = [], []
    h = hold(ml.get("home"), ml.get("away"))
    hold_tag = None
    if h is not None and hold_cut:
        hold_tag = "high hold" if h >= hold_cut[1] else "low hold" if h <= hold_cut[0] else None
    for side in ("home", "away"):
        p = ml.get(side)
        kind = "dog ML" if _dog(p, *SMALL_DOG) else "lock-price fav ML" if p is not None and -170 <= p <= -120 else None
        if kind:
            every.append((p, won[side]))
            if hold_tag:
                spots.append((f"{kind}: {hold_tag}", p, won[side]))
        if sport in SPREAD_SPORTS:
            sp, r = spread_odds.get(side), spread_cover.get(side)
            if sp is not None and r is not None:
                every.append((sp, r))
                tag = juice_tag(sp)
                if tag:
                    spots.append((f"spread side {tag}", sp, r))
    for k in ("over", "under"):
        tp, r = total_odds.get(k), total_res.get(k)
        if tp is not None and r is not None:
            every.append((tp, r))
            tag = juice_tag(tp)
            if tag:
                spots.append((f"{k} {tag}", tp, r))
    return spots, every


def _espn_rows(folder: Path, sport: str):
    games = load(folder, sport)
    out = []
    for g in games:
        cover = {}
        if g.spread:
            cover = {s: _res(cover_margin(g, s)) for s in ("home", "away")}
        pts = g.hs + g.as_
        tres = {} if g.total is None else {"over": _res(pts - g.total), "under": _res(g.total - pts)}
        out.append((g.season, g.ml, cover, g.spread_odds, tres, g.total_odds, {s: g.won(s) for s in ("home", "away")}))
    return out, TUNE_LAST


def _nfl_rows(folder: Path):
    out = []
    for g in load_nfl(folder):
        if not g.done:
            continue
        m = g.home_score - g.away_score
        won = {"home": _res(m), "away": _res(-m)}
        cover = {}
        if g.spread is not None:  # nflverse: home favored by spread_line
            cover = {"home": _res(m - g.spread), "away": _res(g.spread - m)}
        pts = g.home_score + g.away_score
        tres = {} if g.total is None else {"over": _res(pts - g.total), "under": _res(g.total - pts)}
        out.append((g.season, {"home": g.home_ml, "away": g.away_ml}, cover,
                    {"home": g.home_spread_odds, "away": g.away_spread_odds}, tres,
                    {"over": g.over_odds, "under": g.under_odds}, won))
    return out, NFL_TUNE_LAST


def hold_cutoffs(rows, tune_last: int) -> tuple[float, float] | None:
    xs = sorted(h for r in rows if r[0] <= tune_last for h in [hold(r[1].get("home"), r[1].get("away"))] if h is not None)
    if len(xs) < 80:
        return None
    lo, hi = xs[len(xs) // 4], xs[(3 * len(xs)) // 4]
    return None if lo >= hi else (lo, hi)


def study(sport: str, rows, tune_last: int, recs: dict, control: dict, n_random: int = 100, seed: int = 52) -> None:
    cut = hold_cutoffs(rows, tune_last)
    rng = random.Random(f"{seed}:{sport}")

    def put(book, key, season, price, res):
        arm = "blind" if season > tune_last else "in_sample"
        book.setdefault(key, {"in_sample": Record(), "blind": Record()})[arm].add(price, res)

    for season, ml, cover, sodds, tres, todds, won in rows:
        clean = lambda d: {k: (v if v is not None and abs(v) >= 100 else None) for k, v in d.items()}  # noqa: E731
        spots, every = rows_for(sport, season, clean(ml), cover, clean(sodds), tres, clean(todds), won, cut)
        for k in range(n_random):
            if rng.random() < 0.1:
                for price, res in every:
                    put(control, (sport, k), season, price, res)
        for variant, price, res in spots:
            put(recs, (sport, variant), season, price, res)


def run(espn_dir: Path, nfl_dir: Path | None = None, sports=ESPN_SPORTS, random_n: int = 100) -> dict:
    recs: dict = {}
    control: dict = {}
    for sport in sports:
        folder = Path(espn_dir) / sport
        if any(folder.glob("*.csv.gz")):
            rows, tl = _espn_rows(folder, sport)
            study(sport, rows, tl, recs, control, random_n)
    if nfl_dir is not None and (any(Path(nfl_dir).glob("*.csv")) or any(Path(nfl_dir).glob("*.csv.gz"))):
        rows, tl = _nfl_rows(Path(nfl_dir))
        study("nfl", rows, tl, recs, control, random_n)
    out = [{"study": STUDY, "sport": k[0], "variant": k[1], "verdict": verdict(r, k[1]),
            **{a: r[a].to_dict() for a in r}} for k, r in sorted(recs.items())]
    fakes = [verdict(r) for r in control.values()]
    return {"rows": out, "control": {"spots": len(fakes), "proven": sum(1 for x in fakes if x),
                                     "strong": fakes.count("STRONG")}}
