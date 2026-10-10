"""Fifteen studies of dogs, value and totals on 2018+ closing lines, six sports.

Each study bets one simple rule at the real price (closing, or opening where it says
so) and keeps a record for the tuning seasons and the blind ones. A variant counts as
PROVEN only by the blueprint rule (60+ blind bets, blind ROI minus one standard error
above zero, AND a profit in the tuning seasons).

So many variants are tested that some will pass by luck. To measure that, the same
rule is applied to hundreds of RANDOM spots (a random tenth of the games, dog side or
favorite side). The share of random spots that "pass" is the false-pass rate; a real
edge should also pass the STRONG bar: ROI minus one standard error above zero in the
tuning seasons too, not just a profit. Only STRONG variants are worth money.

Data: data/lines/espn/<sport>/<season>.csv.gz (espnhist.build).
"""

from __future__ import annotations

import csv
import gzip
import math
import random
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .nflhist import PROVEN_BETS, Record
from .odds import no_vig

TUNE_LAST = 2021  # closing lines start 2018: tune 2018-2021, blind 2022 on
OPEN_TUNE_LAST = 2023  # opening lines start 2023 (MLB 2019): tune 2023, blind 2024 on
DOG = (100, 300)
SMALL_DOG = (100, 220)
MAX_FAV = -170
SPORTS = ("nfl", "ncaaf", "nba", "ncaab", "mlb", "nhl")
SPREAD_SPORTS = ("nfl", "ncaaf", "nba", "ncaab")  # MLB/NHL spreads are always 1.5: no information
DAILY = ("nba", "ncaab", "mlb", "nhl")
BLOWOUT = {"nfl": 17, "ncaaf": 24, "nba": 20, "ncaab": 20, "mlb": 7, "nhl": 4}
ALTITUDE_M = 1400


def _f(v) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(x) else x


def _price(v) -> float | None:
    x = _f(v)
    return x if x is not None and abs(x) >= 100 else None


@dataclass
class G:
    sport: str
    gid: str
    season: int
    start: datetime
    home: str
    away: str
    hs: float
    as_: float
    neutral: bool
    ml: dict  # side -> closing price
    open_ml: dict
    spread: float | None  # ESPN sign: home line (negative = home favored)
    spread_odds: dict
    total: float | None
    total_odds: dict  # over/under
    rest: dict
    elev: float | None
    tzo: float | None
    temp: float | None
    wind: float | None
    rain: float | None
    indoor: bool
    starter: dict  # MLB pitcher / NHL goalie
    stype: str
    h1_ml: dict
    h1_spread: float | None
    h1_spread_odds: dict
    h1: dict  # side -> first-half points
    ctx: dict = field(default_factory=dict)  # features from earlier games, filled by annotate()

    @property
    def margin(self) -> float:
        return self.hs - self.as_

    def won(self, side: str) -> float:
        m = self.margin if side == "home" else -self.margin
        return 1.0 if m > 0 else 0.0 if m < 0 else 0.5


def load(folder: Path, sport: str) -> list[G]:
    out = []
    for f in sorted(Path(folder).glob("*.csv.gz")):
        with gzip.open(f, "rt", newline="") as fh:
            for r in csv.DictReader(fh):
                hs, as_ = _f(r["home_score"]), _f(r["away_score"])
                if hs is None or as_ is None or not r.get("start"):
                    continue
                pair = lambda a, b: {"home": _price(r.get(a)), "away": _price(r.get(b))}  # noqa: E731
                line = _f(r.get("spread_line"))
                out.append(G(
                    sport, r["game_id"], int(r["season"]), datetime.fromisoformat(r["start"].replace("Z", "+00:00")),
                    r["home_team"], r["away_team"], hs, as_, r["location"] == "Neutral",
                    pair("home_moneyline", "away_moneyline"), pair("open_home_ml", "open_away_ml"),
                    None if line is None else -line, pair("home_spread_odds", "away_spread_odds"),
                    _f(r.get("total_line")), {"over": _price(r.get("over_odds")), "under": _price(r.get("under_odds"))},
                    {"home": _f(r.get("home_rest")), "away": _f(r.get("away_rest"))},
                    _f(r.get("elev")), _f(r.get("tzo")), _f(r.get("wx_temp")), _f(r.get("wx_wind")),
                    _f(r.get("wx_rain")), r.get("roof") == "dome", {"home": r.get("sp_home") or "", "away": r.get("sp_away") or ""},
                    r.get("stype") or "", pair("h1_ml_home", "h1_ml_away"), _f(r.get("h1_spread_home")),
                    pair("h1_spread_home_odds", "h1_spread_away_odds"), {"home": _f(r.get("h1_home")), "away": _f(r.get("h1_away"))},
                ))
    return sorted(out, key=lambda g: (g.start, g.gid))


def _other(side: str) -> str:
    return "away" if side == "home" else "home"


def annotate(games: list[G]) -> None:
    """Features each game knew before it started: last result, streaks, schedule density,
    home time zone, games played this season, and each starter's usual and last outing."""
    last: dict[str, tuple] = {}
    streak: Counter = Counter()  # + wins in a row, - losses in a row
    recent: dict[str, deque] = defaultdict(lambda: deque(maxlen=4))
    zones: dict[str, Counter] = defaultdict(Counter)
    played: Counter = Counter()
    starters: dict[str, deque] = defaultdict(lambda: deque(maxlen=10))
    outing: dict[str, float] = {}  # starter -> runs/goals allowed last start
    season = None
    for g in games:
        if g.season != season:
            season = g.season
            streak.clear()
            played.clear()
            last.clear()
        teams = {"home": g.home, "away": g.away}
        for side, t in teams.items():
            prev = last.get(t)
            starts = list(starters[t])
            usual = max(set(starts), key=starts.count) if len(starts) >= 5 else None
            g.ctx[side] = {
                "last_margin": prev[0] if prev else None,
                "last_upset": prev[1] if prev else False,
                "streak": streak[t],
                "in4": sum((g.start - d).total_seconds() < 4 * 86400 for d in recent[t]),
                "zone": zones[t].most_common(1)[0][0] if zones[t] else None,
                "played": played[t],
                "backup": bool(usual and g.starter[side] and g.starter[side] != usual),
                "starter_last": outing.get(g.starter[side]) if g.starter[side] else None,
            }
        if g.tzo is not None and not g.neutral:
            zones[g.home][g.tzo] += 1
        for side, t in teams.items():
            m = g.margin if side == "home" else -g.margin
            price = g.ml.get(side)
            last[t] = (m, bool(price and price > 0 and m > 0))
            streak[t] = (max(streak[t], 0) + 1) if m > 0 else (min(streak[t], 0) - 1) if m < 0 else 0
            recent[t].append(g.start)
            played[t] += 1
            if g.starter[side]:
                starters[t].append(g.starter[side])
                outing[g.starter[side]] = g.as_ if side == "home" else g.hs


# --------------------------------------------------------------------- record

class Book:
    """Records per (study, variant, sport): tuning vs blind seasons."""

    def __init__(self):
        self.recs: dict[tuple, dict[str, Record]] = defaultdict(lambda: {"in_sample": Record(), "blind": Record()})

    def add(self, study: str, variant: str, g: G, price: float | None, result: float, blind_from: int) -> None:
        if price is None:
            return
        self.recs[(study, variant, g.sport)]["blind" if g.season >= blind_from else "in_sample"].add(price, result)


CEILING = "ceiling: "  # variants that peek at the future: a yardstick, never a bet


def verdict(rec: dict[str, Record], variant: str = "") -> str:
    if variant.startswith(CEILING):
        return ""
    b, t = rec["blind"], rec["in_sample"]
    if not (b.proven and (t.flat_roi or 0) > 0):
        return ""
    return "STRONG" if t.bets >= PROVEN_BETS and t.flat_roi - (t.se or 0) > 0 else "PROVEN"


def _dog(price, lo=DOG[0], hi=DOG[1]) -> bool:
    return price is not None and lo <= price <= hi


def _fav(price) -> bool:
    return price is not None and MAX_FAV <= price < 0


def _fair(prices: dict) -> dict | None:
    return no_vig(prices) if prices.get("home") and prices.get("away") else None


def _phi(z: float) -> float:
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def margin_sd(games: list[G]) -> float:
    errs = [g.margin + g.spread for g in games if g.spread is not None and g.season <= TUNE_LAST]
    return math.sqrt(sum(e * e for e in errs) / len(errs)) if len(errs) > 30 else 12.0


# -------------------------------------------------------------------- studies

def run_studies(games: list[G], sport: str, book: Book) -> None:
    sd = margin_sd(games) if sport in SPREAD_SPORTS else None
    open_blind = TUNE_LAST + 1 if sport == "mlb" else OPEN_TUNE_LAST + 1
    totals = [g.total for g in games if g.total]
    hi_total = sorted(totals)[int(len(totals) * 0.8)] if totals else None
    lo_total = sorted(totals)[int(len(totals) * 0.2)] if totals else None
    B = TUNE_LAST + 1
    for g in games:
        sides = ("home", "away")
        fair = _fair(g.ml)
        # 1. Price bands: is any band of dogs or favorites mispriced on its own?
        for side in sides:
            p = g.ml[side]
            if p is None:
                continue
            for lo, hi in ((100, 130), (131, 170), (171, 220), (221, 300)):
                if lo <= p <= hi:
                    book.add("01 price band", f"dog +{lo}..+{hi}", g, p, g.won(side), B)
            for lo, hi in ((-130, -100), (-170, -131)):
                if lo <= p <= hi:
                    book.add("01 price band", f"fav {lo}..{hi}", g, p, g.won(side), B)
            if side == "home" and _dog(p):
                book.add("01 price band", "home dog", g, p, g.won(side), B)
            if side == "away" and _dog(p):
                book.add("01 price band", "road dog", g, p, g.won(side), B)
        # 2. Cross-market value: moneyline cheaper than the spread says.
        if sd and fair and g.spread is not None:
            p_home = _phi(-g.spread / sd)
            for side in sides:
                gap = (p_home if side == "home" else 1 - p_home) - fair[side]
                p = g.ml[side]
                for need in (0.02, 0.04):
                    if gap >= need and _dog(p):
                        book.add("02 spread-vs-ML value", f"dog, spread says {need:.0%}+ more", g, p, g.won(side), B)
                    if gap >= need and _fav(p):
                        book.add("02 spread-vs-ML value", f"fav, spread says {need:.0%}+ more", g, p, g.won(side), B)
        # 3 + 4. Opening lines: follow or fade the move; bet at the open.
        ofair = _fair(g.open_ml)
        if fair and ofair:
            for side in sides:
                move = fair[side] - ofair[side]
                p = g.ml[side]
                for size in (0.03, 0.06):
                    if move >= size and (_dog(p) or _fav(p)):
                        book.add("03 line move", f"follow a {size:.0%}+ move (at the close)", g, p, g.won(side), open_blind)
                    if move <= -size and _dog(p):
                        book.add("03 line move", f"dog the line drifted from {size:.0%}+ (at the close)", g, p,
                                 g.won(side), open_blind)
                op = g.open_ml[side]
                if _dog(op, *SMALL_DOG):
                    book.add("04 bet the opener", "dog +100..+220 at the open", g, op, g.won(side), open_blind)
                if _fav(op):
                    book.add("04 bet the opener", "favorite to -170 at the open", g, op, g.won(side), open_blind)
                if move >= 0.03 and _dog(op, *SMALL_DOG):
                    # Not bettable blind (the move is future), kept as a ceiling: what early money is worth.
                    book.add("04 bet the opener", "ceiling: open dog the money later hit", g, op, g.won(side), open_blind)
        # 5. Totals: unders and overs by size.
        if g.total and g.total_odds["under"] and g.total_odds["over"]:
            pts = g.hs + g.as_
            under = 1.0 if pts < g.total else 0.5 if pts == g.total else 0.0
            over = 1.0 - under if under != 0.5 else 0.5
            book.add("05 totals", "every under", g, g.total_odds["under"], under, B)
            if hi_total and g.total >= hi_total:
                book.add("05 totals", "under, highest 20% of totals", g, g.total_odds["under"], under, B)
            if lo_total and g.total <= lo_total:
                book.add("05 totals", "over, lowest 20% of totals", g, g.total_odds["over"], over, B)
            # 6. Weather totals (outdoor).
            if not g.indoor and sport in ("nfl", "ncaaf", "mlb"):
                if g.wind is not None and g.wind >= 15:
                    book.add("06 weather totals", "under, wind 15+ mph", g, g.total_odds["under"], under, B)
                if g.temp is not None and g.temp <= 40:
                    book.add("06 weather totals", "under, 40F or colder", g, g.total_odds["under"], under, B)
                if g.temp is not None and g.temp >= 85:
                    book.add("06 weather totals", "over, 85F or hotter", g, g.total_odds["over"], over, B)
                if g.rain is not None and g.rain >= 1.0:
                    book.add("06 weather totals", "under, rain", g, g.total_odds["under"], under, B)
        for side in sides:
            p = g.ml[side]
            c, o = g.ctx[side], g.ctx[_other(side)]
            # 7. Altitude: road teams up high.
            if g.elev is not None and g.elev >= ALTITUDE_M and not g.neutral and side == "home":
                if _dog(p):
                    book.add("07 altitude", "home dog at altitude", g, p, g.won(side), B)
                if _fav(p):
                    book.add("07 altitude", "home favorite at altitude", g, p, g.won(side), B)
            # 8. Travel: road team far from its home time zone.
            if side == "away" and not g.neutral and c["zone"] is not None and g.tzo is not None:
                shift = g.tzo - c["zone"]  # + = flew east
                if shift >= 2 and _dog(p, *SMALL_DOG):
                    book.add("08 travel", "road dog flew 2+ zones east", g, p, g.won(side), B)
                if shift <= -2 and _dog(p, *SMALL_DOG):
                    book.add("08 travel", "road dog flew 2+ zones west", g, p, g.won(side), B)
                hp = g.ml["home"]
                if abs(shift) >= 2 and _dog(hp, *SMALL_DOG):
                    book.add("08 travel", "home dog vs a team 2+ zones from home", g, hp, g.won("home"), B)
            # 9. Rest and schedule density (daily sports).
            if sport in DAILY and _dog(p, *SMALL_DOG):
                r, ro = g.rest[side], g.rest[_other(side)]
                if r is not None and ro is not None:
                    if r >= 2 and ro == 1:
                        book.add("09 rest", "rested dog vs a back-to-back", g, p, g.won(side), B)
                    if r >= ro + 2:
                        book.add("09 rest", "dog with 2+ more days of rest", g, p, g.won(side), B)
                    if r == 1 and ro == 1:
                        book.add("09 rest", "dog, both on a back-to-back", g, p, g.won(side), B)
                if o["in4"] >= 2 and c["in4"] <= 1:
                    book.add("09 rest", "dog vs a team on its 3rd game in 4 days", g, p, g.won(side), B)
            if sport in ("nfl", "ncaaf") and _dog(p, *SMALL_DOG):
                r, ro = g.rest[side], g.rest[_other(side)]
                if r is not None and ro is not None and r >= ro + 3:
                    book.add("09 rest", "football dog with 3+ more days of rest", g, p, g.won(side), B)
            # 10. Bounce-back and letdown.
            if _dog(p, *SMALL_DOG):
                lm, olm = c["last_margin"], o["last_margin"]
                if lm is not None and lm <= -BLOWOUT[sport]:
                    book.add("10 bounce-back", "dog after getting blown out", g, p, g.won(side), B)
                if olm is not None and olm >= BLOWOUT[sport]:
                    book.add("10 bounce-back", "dog vs a team off a blowout win", g, p, g.won(side), B)
                if c["last_upset"]:
                    book.add("10 bounce-back", "dog off an upset win (as a dog)", g, p, g.won(side), B)
                # 11. Streaks.
                if o["streak"] >= 5:
                    book.add("11 streaks", "dog vs a team on a 5+ win streak", g, p, g.won(side), B)
                if c["streak"] <= -5:
                    book.add("11 streaks", "dog on a 5+ losing streak", g, p, g.won(side), B)
                if c["streak"] >= 3:
                    book.add("11 streaks", "dog on a 3+ win streak", g, p, g.won(side), B)
            # 12. Starters: NHL goalie / MLB pitcher.
            if sport in ("nhl", "mlb"):
                if o["backup"] and _dog(p, *SMALL_DOG):
                    book.add("12 starters", "dog vs a team NOT starting its usual starter", g, p, g.won(side), B)
                if c["backup"] and _fav(g.ml[_other(side)]):
                    book.add("12 starters", "favorite vs a team starting a backup", g, g.ml[_other(side)],
                             g.won(_other(side)), B)
                shelled = {"mlb": 6, "nhl": 5}[sport]
                if c["starter_last"] is not None and c["starter_last"] >= shelled and _dog(p, *SMALL_DOG):
                    book.add("12 starters", "dog whose starter got shelled last time", g, p, g.won(side), B)
                if o["starter_last"] is not None and o["starter_last"] == 0 and _dog(p, *SMALL_DOG):
                    book.add("12 starters", "dog vs a starter off a shutout", g, p, g.won(side), B)
            # 14. Season stage.
            if _dog(p, *SMALL_DOG):
                n = {"nfl": 2, "ncaaf": 2, "nba": 8, "ncaab": 4, "mlb": 10, "nhl": 8}[sport]
                if c["played"] < n and o["played"] < n:
                    book.add("14 season stage", "dog early in the season", g, p, g.won(side), B)
                if g.stype == "3":
                    book.add("14 season stage", "playoff dog", g, p, g.won(side), B)
                if g.neutral:
                    book.add("14 season stage", "neutral-site dog", g, p, g.won(side), B)
        # 13. First half / first five: dogs and spreads.
        if None not in g.h1.values():
            h1m = g.h1["home"] - g.h1["away"]
            for side in sides:
                hp = g.h1_ml[side]
                res = 1.0 if (h1m if side == "home" else -h1m) > 0 else 0.5 if h1m == 0 else 0.0
                if _dog(hp, *SMALL_DOG):
                    book.add("13 first half", "first-half dog moneyline", g, hp, res, B)
            if g.h1_spread is not None and g.h1_spread_odds["home"] and g.h1_spread_odds["away"] and sport in SPREAD_SPORTS:
                dog = "home" if g.h1_spread > 0 else "away" if g.h1_spread < 0 else None
                if dog:
                    cover = (h1m + g.h1_spread) if dog == "home" else (-h1m - g.h1_spread)
                    res = 1.0 if cover > 0 else 0.5 if cover == 0 else 0.0
                    book.add("13 first half", "first-half spread dog", g, g.h1_spread_odds[dog], res, B)
        # 15. Spreads: big dogs, key numbers.
        if g.spread is not None and g.spread_odds["home"] and g.spread_odds["away"] and sport in SPREAD_SPORTS:
            dog = "home" if g.spread > 0 else "away" if g.spread < 0 else None
            if dog:
                pts = abs(g.spread)
                cover = (g.margin + g.spread) if dog == "home" else (-g.margin - g.spread)
                res = 1.0 if cover > 0 else 0.5 if cover == 0 else 0.0
                odds = g.spread_odds[dog]
                big = {"nfl": 10, "ncaaf": 21, "nba": 10, "ncaab": 15}[sport]
                if pts >= big:
                    book.add("15 spreads", f"big spread dog ({big}+)", g, odds, res, B)
                if pts <= 3:
                    book.add("15 spreads", "small spread dog (3 or less)", g, odds, res, B)
                if sport in ("nfl", "ncaaf") and pts in (3.0, 3.5, 7.0, 7.5):
                    book.add("15 spreads", "dog on a key number (3/3.5/7/7.5)", g, odds, res, B)
                if dog == "home":
                    book.add("15 spreads", "home spread dog", g, odds, res, B)


def random_spots(games: list[G], sport: str, book: Book, n: int = 100, seed: int = 7) -> None:
    """The luck control: n random tenths of the games, betting the dog (half) or favorite."""
    rng = random.Random(f"{seed}:{sport}")
    picks = [(rng.random() < 0.5, [rng.random() < 0.1 for _ in games]) for _ in range(n)]
    for i, g in enumerate(games):
        for k, (dogs, chosen) in enumerate(picks):
            if not chosen[i]:
                continue
            for side in ("home", "away"):
                p = g.ml[side]
                if (dogs and _dog(p, *SMALL_DOG)) or (not dogs and _fav(p)):
                    book.add("random", f"random {k}", g, p, g.won(side), TUNE_LAST + 1)


def run(lines_dir: Path, sports=SPORTS, random_n: int = 100) -> dict:
    book, control = Book(), Book()
    for sport in sports:
        folder = Path(lines_dir) / sport
        if not any(folder.glob("*.csv.gz")):
            continue
        games = load(folder, sport)
        annotate(games)
        run_studies(games, sport, book)
        random_spots(games, sport, control, random_n)
    rows = []
    for (study, variant, sport), rec in sorted(book.recs.items()):
        rows.append({"study": study, "variant": variant, "sport": sport, "verdict": verdict(rec, variant),
                     **{k: r.to_dict() for k, r in rec.items()}})
    fakes = [verdict(r) for r in control.recs.values()]
    return {
        "rows": rows,
        "control": {"spots": len(fakes), "proven": fakes.count("PROVEN") + fakes.count("STRONG"),
                    "strong": fakes.count("STRONG")},
    }


def report(res: dict) -> str:
    def rec(d):
        roi = f"{d['flat_roi']:+.1%} ± {d['se']:.1%}" if d["flat_roi"] is not None and d["se"] else "-"
        return f"{d['bets']:>5} {roi:>16}"

    out = [f"{'study':24}{'variant':46}{'sport':7}{'tune bets/ROI':>22}{'blind bets/ROI':>22}  verdict", "-" * 130]
    for r in res["rows"]:
        out.append(f"{r['study']:24}{r['variant'][:45]:46}{r['sport']:7}{rec(r['in_sample']):>22}"
                   f"{rec(r['blind']):>22}  {r['verdict']}")
    c = res["control"]
    tests = sum(1 for r in res["rows"] if not r["variant"].startswith(CEILING))
    rate = c["proven"] / c["spots"] if c["spots"] else 0
    srate = c["strong"] / c["spots"] if c["spots"] else 0
    passed = sum(1 for r in res["rows"] if r["verdict"])
    strong = sum(1 for r in res["rows"] if r["verdict"] == "STRONG")
    out += ["", f"Luck control: {c['proven']} of {c['spots']} random spots passed ({rate:.1%}), {c['strong']} STRONG "
            f"({srate:.1%}).",
            f"Real studies: {passed} of {tests} variants passed; luck alone would pass about {rate * tests:.1f}. "
            f"STRONG: {strong}; luck about {srate * tests:.1f}."]
    return "\n".join(out)


def strong_rows(res: dict) -> list[dict]:
    return [r for r in res.get("rows", []) if r["verdict"] == "STRONG"]


# Tested variant -> live spot key (spots.py). A STRONG variant sets that spot's weight.
SPOT_KEYS = {
    ("09 rest", "rested dog vs a back-to-back"): "rested_dog",
    ("06 weather totals", "under, wind 15+ mph"): "wind_under",
    ("08 travel", "road dog flew 2+ zones west"): "east_traveler_dog",
    ("08 travel", "home dog vs a team 2+ zones from home"): "west_host_dog",
}
TOTAL_DECIMAL = 1.9  # about -110
DOG_DECIMAL = 2.5  # about +150


def spot_weights(res: dict | None) -> dict[str, float]:
    """{"sport:spot": win-chance bump} for STRONG variants that the live board can spot.

    One standard error is shaved off the blind ROI, and the rest is turned into
    win-chance points at a typical price for that bet."""
    out = {}
    for r in (res or {}).get("rows", []):
        key = SPOT_KEYS.get((r["study"], r["variant"]))
        if key and r["verdict"] == "STRONG":
            b = r["blind"]
            w = (b["flat_roi"] - (b["se"] or 0)) / (TOTAL_DECIMAL if key == "wind_under" else DOG_DECIMAL)
            if w > 0:
                out[f"{r['sport']}:{key}"] = w
    return out
