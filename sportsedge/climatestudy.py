"""Climate shock: teams playing in weather they never see at home (study of 2026-11-16).

Idea: a dome team or a warm-weather team that has to play outdoors in the cold (or a cold-weather
team sent into the heat) may play worse than the market thinks. Does the home side win more than its
price says, does the visiting dog lose more, do those games go under?

Each team's home climate is read from its EARLIER home games only (never this game or later ones):
  indoor  - most of its earlier home games were under a roof (dome / closed retractable)
  warm    - outdoor home, its earlier late-season (Nov-Jan) home games averaged 58F or warmer
  cold    - outdoor home, those games averaged 45F or colder
A team needs MIN_HOME earlier home games before it is classed. Spots, for the ROAD side of an
outdoor, non-neutral game with a known kickoff temperature:
  cold shock     - indoor or warm visitor, 40F or colder
  freezing shock - indoor or warm visitor, 32F or colder
  heat shock     - cold visitor, 80F or hotter
  wind shock     - indoor visitor, wind 15+ mph
Tested on the road side and the home side: +100..+220 moneyline dog, -170..-120 favorite, spread at
the real price, plus the game's under and over. NFL uses nflverse 2006+ (tune 2006-2017, blind 2018+,
like the NFL history test); college football uses the ESPN archive with weather (tune through 2021,
blind 2022+). Luck control: random tenths of every bet through the same bar.
"""

from __future__ import annotations

import random
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from . import linestudies as LS
from . import nflhist
from .nflhist import Record

STUDY = "44 climate shock"
MIN_HOME = 4
WARM_F, COLD_F = 58.0, 45.0
SPOTS = ("cold shock", "freezing shock", "heat shock", "wind shock")
BLIND_FROM = {"nfl": nflhist.TUNE_LAST + 1, "ncaaf": LS.TUNE_LAST + 1}


def from_nflverse(games: list) -> list[LS.G]:
    """nflverse games as archive rows (G). nflverse spread_line = home favored by; G uses the home line."""
    out = []
    for g in games:
        if not g.done:
            continue
        temp = None
        if g.outdoors:
            temp = getattr(g, "temp", None)
        out.append(LS.G(
            "nfl", g.gid, g.season, datetime(g.day.year, g.day.month, g.day.day, 18, tzinfo=timezone.utc),
            g.home, g.away, g.home_score, g.away_score, g.neutral,
            {"home": g.home_ml, "away": g.away_ml}, {"home": None, "away": None},
            None if g.spread is None else -g.spread, {"home": g.home_spread_odds, "away": g.away_spread_odds},
            g.total, {"over": g.over_odds, "under": g.under_odds}, {"home": g.home_rest, "away": g.away_rest},
            None, None, temp, g.wind, None, not g.outdoors, {"home": "", "away": ""}, "",
            {"home": None, "away": None}, None, {"home": None, "away": None}, {"home": None, "away": None}))
    return out


def load_nfl(folder: Path) -> list[LS.G]:
    """nflverse rows with the kickoff temperature (the plain loader skips it)."""
    import csv
    import gzip

    temps = {}
    for f in sorted(Path(folder).glob("*.csv")) + sorted(Path(folder).glob("*.csv.gz")):
        opener = gzip.open if f.suffix == ".gz" else open
        with opener(f, "rt", newline="") as fh:
            for r in csv.DictReader(fh):
                temps[r.get("game_id", "")] = nflhist._f(r.get("temp"))
    games = nflhist.load(folder)
    for g in games:
        g.temp = temps.get(g.gid)
    return from_nflverse(games)


def climates(games: list[LS.G]) -> list[dict]:
    """Each game -> {side: 'indoor' | 'warm' | 'cold' | None} from that team's earlier home games only."""
    hist: dict[str, dict] = defaultdict(lambda: {"n": 0, "roof": 0, "late": []})
    out = []
    for g in games:
        row = {}
        for side, team in (("home", g.home), ("away", g.away)):
            h = hist[team]
            if h["n"] < MIN_HOME:
                row[side] = None
            elif h["roof"] * 2 > h["n"]:
                row[side] = "indoor"
            elif len(h["late"]) >= 2:
                avg = sum(h["late"]) / len(h["late"])
                row[side] = "warm" if avg >= WARM_F else "cold" if avg <= COLD_F else None
            else:
                row[side] = None
        out.append(row)
        if not g.neutral:
            h = hist[g.home]
            h["n"] += 1
            h["roof"] += bool(g.indoor)
            if not g.indoor and g.temp is not None and g.start.month in (11, 12, 1):
                h["late"].append(g.temp)
    return out


def shock_spots(g: LS.G, clim: dict) -> list[str]:
    """Spots for the visiting side of an outdoor, non-neutral game."""
    if g.neutral or g.indoor:
        return []
    c = clim.get("away")
    out = []
    if g.temp is not None:
        if c in ("indoor", "warm") and g.temp <= 40:
            out.append("cold shock")
        if c in ("indoor", "warm") and g.temp <= 32:
            out.append("freezing shock")
        if c == "cold" and g.temp >= 80:
            out.append("heat shock")
    if c == "indoor" and g.wind is not None and g.wind >= 15:
        out.append("wind shock")
    return out


def bets(g: LS.G) -> list[tuple[str, float, float]]:
    """(kind, price, result) for every bet the game offers that the study scores."""
    out = []
    for side, tag in (("away", "visitor"), ("home", "host")):
        p = g.ml.get(side)
        if p is not None and LS.SMALL_DOG[0] <= p <= LS.SMALL_DOG[1]:
            out.append((f"{tag} dog ML", p, g.won(side)))
        elif p is not None and -170 <= p <= -120:
            out.append((f"{tag} lock-price fav ML", p, g.won(side)))
        sp = g.spread_odds.get(side) if g.spread is not None else None
        if sp:
            m = g.margin + g.spread if side == "home" else -g.margin - g.spread
            out.append((f"{tag} spread", sp, 1.0 if m > 0 else 0.0 if m < 0 else 0.5))
    if g.total is not None:
        pts = g.hs + g.as_
        res = 0.5 if pts == g.total else None
        for k in ("under", "over"):
            price = g.total_odds.get(k)
            if price:
                r = res if res is not None else float((pts < g.total) if k == "under" else (pts > g.total))
                out.append((k, price, r))
    return out


def study(games: list[LS.G], sport: str, recs: dict, control: dict, n_random: int = 100, seed: int = 44) -> None:
    games = sorted(games, key=lambda g: (g.start, g.gid))
    clim = climates(games)
    blind_from = BLIND_FROM[sport]
    rng = random.Random(f"{seed}:{sport}")

    def put(book, key, g, price, res):
        arm = "blind" if g.season >= blind_from else "in_sample"
        book.setdefault(key, {"in_sample": Record(), "blind": Record()})[arm].add(price, res)

    for i, g in enumerate(games):
        kinds = bets(g)
        picks = [rng.random() < 0.1 for _ in range(n_random)]
        for k in range(n_random):
            if picks[k]:
                for _, price, res in kinds:
                    put(control, f"random {k}", g, price, res)
        for s in shock_spots(g, clim[i]):
            for kind, price, res in kinds:
                put(recs, (sport, f"{kind}: {s}"), g, price, res)


def run(data_dir: Path, random_n: int = 100) -> dict:
    data_dir = Path(data_dir)
    recs: dict = {}
    control: dict = {}
    nfl = data_dir / "lines" / "nfl"
    if any(nfl.glob("*.csv*")):
        study(load_nfl(nfl), "nfl", recs, control, random_n)
    cfb = data_dir / "lines" / "espn" / "ncaaf"
    if any(cfb.glob("*.csv.gz")):
        study(LS.load(cfb, "ncaaf"), "ncaaf", recs, control, random_n)
    rows = [{"study": STUDY, "sport": k[0], "variant": k[1], "verdict": LS.verdict(r, k[1]),
             **{a: r[a].to_dict() for a in r}} for k, r in sorted(recs.items())]
    fakes = [LS.verdict(r) for r in control.values()]
    return {"rows": rows, "control": {"spots": len(fakes), "proven": sum(1 for x in fakes if x),
                                      "strong": fakes.count("STRONG")}}
