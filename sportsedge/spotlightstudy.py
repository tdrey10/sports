"""Spotlight hangover: the game after a primetime or late-night game (study of 2026-11-24).

Idea: a team that just played under the lights (NFL/college football night game, a 10pm-Eastern
West-coast tip or puck drop, an MLB night game followed by a day game) may be flat or tired next
time out, and the market may not price it. Or the market over-reacts to the big-stage game.

Spots, from each team's PREVIOUS game this season (known before kickoff), each side at the real
closing price:
  - football (NFL, college football): previous game started 7:30pm Eastern or later ("off a night game")
  - NBA, NHL, college hoops: previous game started 10pm Eastern or later and today's game starts
    within 48 hours of it ("off a late-night game")
  - MLB: previous game started 6pm Eastern or later and today's game starts before 5pm Eastern
    on the very next day ("day game after a night game")
For the spot side and against it (the opponent of a spot side that is not in the spot itself):
+100..+220 dog ML, -170..-120 favorite ML, spread side (football and hoops); plus over / under
when either team is in the spot.
Data: ESPN closing lines (data/lines/espn, 2016+): tune through linestudies.TUNE_LAST, blind after.
Luck control: random tenths of all bets per sport through the same bar.
"""

from __future__ import annotations

import random
from datetime import timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .atsstudy import cover_margin
from .linestudies import SMALL_DOG, SPORTS, SPREAD_SPORTS, TUNE_LAST, G, _dog, load, verdict
from .nflhist import Record

STUDY = "53 spotlight hangover"
EASTERN = ZoneInfo("America/New_York")
SPOT = {"nfl": "off a night game", "ncaaf": "off a night game", "nba": "off a late-night game",
        "nhl": "off a late-night game", "ncaab": "off a late-night game", "mlb": "day game after a night game"}


def _et(dt):
    t = dt.astimezone(EASTERN)
    return t.hour + t.minute / 60, t.date()


def in_spot(sport: str, prev: G | None, g: G) -> bool:
    """Is a team whose previous game was `prev` in the spotlight-hangover spot for game `g`?"""
    if prev is None or prev.season != g.season or prev.start >= g.start:
        return False
    ph, pd = _et(prev.start)
    if sport in ("nfl", "ncaaf"):
        return ph >= 19.5
    if sport in ("nba", "nhl", "ncaab"):
        return ph >= 22 and g.start - prev.start <= timedelta(hours=48)
    if sport == "mlb":
        h, d = _et(g.start)
        return ph >= 18 and h < 17 and (d - pd).days == 1
    return False


def _res(m: float | None) -> float | None:
    return None if m is None else 1.0 if m > 0 else 0.0 if m < 0 else 0.5


def bets(g: G, side: str) -> list[tuple[str, float, float]]:
    """Every bet on `side` in game g: (kind, price, result)."""
    out = []
    p = g.ml.get(side)
    if _dog(p, *SMALL_DOG):
        out.append(("dog ML", p, g.won(side)))
    elif p is not None and -170 <= p <= -120:
        out.append(("lock-price fav ML", p, g.won(side)))
    if g.sport in SPREAD_SPORTS and g.spread is not None:
        sp, r = g.spread_odds.get(side), _res(cover_margin(g, side))
        if sp is not None and r is not None:
            out.append(("spread side", sp, r))
    return out


def totals(g: G) -> list[tuple[str, float, float]]:
    if g.total is None:
        return []
    pts = g.hs + g.as_
    out = []
    for k, m in (("over", pts - g.total), ("under", g.total - pts)):
        if g.total_odds.get(k) is not None:
            out.append((k, g.total_odds[k], _res(m)))
    return out


def spots_for(sport: str, g: G, prev: dict) -> tuple[list, list]:
    """-> (spot bets [(variant, price, result)], every bet [(price, result)]) for game g.
    prev: side -> that team's previous game (or None)."""
    tag = SPOT[sport]
    hit = {s: in_spot(sport, prev.get(s), g) for s in ("home", "away")}
    spots, every = [], []
    for side, other in (("home", "away"), ("away", "home")):
        for kind, price, res in bets(g, side):
            every.append((price, res))
            if hit[side]:
                spots.append((f"{kind}: {tag}", price, res))
            elif hit[other]:
                spots.append((f"{kind}: vs a team {tag}", price, res))
    for kind, price, res in totals(g):
        every.append((price, res))
        if hit["home"] or hit["away"]:
            spots.append((f"{kind}: a team {tag}", price, res))
    return spots, every


def study(games: list[G], sport: str, recs: dict, control: dict, n_random: int = 100, seed: int = 53) -> None:
    rng = random.Random(f"{seed}:{sport}")
    last: dict[str, G] = {}

    def put(book, key, season, price, res):
        arm = "blind" if season > TUNE_LAST else "in_sample"
        book.setdefault(key, {"in_sample": Record(), "blind": Record()})[arm].add(price, res)

    for g in sorted(games, key=lambda x: (x.start, x.gid)):
        prev = {"home": last.get(g.home), "away": last.get(g.away)}
        spots, every = spots_for(sport, g, prev)
        for k in range(n_random):
            if rng.random() < 0.1:
                for price, res in every:
                    put(control, (sport, k), g.season, price, res)
        for variant, price, res in spots:
            put(recs, (sport, variant), g.season, price, res)
        last[g.home] = g
        last[g.away] = g


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
