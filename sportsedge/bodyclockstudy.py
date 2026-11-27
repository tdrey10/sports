"""Body clock: the visitor's start time on its OWN home clock (study of 2026-11-27).

Idea: a road team two or more time zones from home plays at an hour its body isn't used to. A West
Coast team in a 1pm Eastern game is playing at 10am back home; an East Coast team in a 10pm-Eastern
tip is playing at what feels like bedtime. Sports science says reaction time and strength peak in
the late afternoon/evening on the body's own clock (the old NFL "West Coast teams at night" story).
Earlier studies tested time zones crossed (08 travel) and the Eastern start slot (slotstudy), never
the hour on the visitor's own clock.

Each team's home zone is its most common STANDARD UTC offset over its EARLIER non-neutral home games
(ESPN `tzo` with US daylight time taken off; 3+ home games needed). The visitor's body-clock hour is
the kickoff in UTC plus that zone (plus daylight time on the game's date). Spots, ROAD side of any
game (a neutral game counts: the "away" team is still away from home):
  - west visitor, body clock before 11am  (home 2+ zones west of the venue, kickoff before 11 its time)
  - west visitor, night game              (home 2+ zones west, 8pm+ at the venue: ~5-6pm its time)
  - east visitor, body clock 10pm+        (home 2+ zones east, kickoff 10pm or later its time)
  - any visitor, body clock before noon   (any zone gap, kickoff before noon its time)
For the visitor (spot side) and the home team against it: +100..+220 dog ML, -170..-120 favorite ML,
spread side (football, hoops), plus the game's over and under.
Data: ESPN closing lines (data/lines/espn, 2016+): tune through linestudies.TUNE_LAST, blind after.
Luck control: random tenths of all bets per sport through the same bar.
"""

from __future__ import annotations

import random
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path

from .linestudies import SPORTS, TUNE_LAST, G, load, verdict
from .spotlightstudy import bets, totals

STUDY = "57 body clock"
MIN_HOME = 3
SPOTS = ("west visitor, body clock before 11am", "west visitor, night game",
         "east visitor, body clock 10pm+", "any visitor, body clock before noon")


def _nth_sunday(year: int, month: int, n: int) -> date:
    d = date(year, month, 1)
    d += timedelta(days=(6 - d.weekday()) % 7)
    return d + timedelta(weeks=n - 1)


def us_dst(d: date) -> bool:
    """US daylight time: second Sunday of March through the day before the first Sunday of November."""
    return _nth_sunday(d.year, 3, 2) <= d < _nth_sunday(d.year, 11, 1)


def standard(tzo: float, d: date) -> float:
    """The venue's standard-time UTC offset (ESPN tzo includes daylight time)."""
    return tzo - (1 if us_dst(d) else 0)


def clock(g: G, visitor_std: float | None) -> dict | None:
    """-> {'gap': visitor zone minus venue zone (negative = visitor from the west), 'venue_h', 'body_h'}."""
    if g.tzo is None or visitor_std is None:
        return None
    day = g.start.date()
    venue_std = standard(g.tzo, day)
    dst = 1 if us_dst(day) else 0
    utc_h = g.start.hour + g.start.minute / 60
    return {"gap": visitor_std - venue_std, "venue_h": (utc_h + g.tzo) % 24,
            "body_h": (utc_h + visitor_std + dst) % 24}


def spots_in(c: dict | None) -> list[str]:
    if c is None:
        return []
    out = []
    # Hours after 4am count as "that day" (a 1am body-clock tip is late, not early).
    body = c["body_h"] if c["body_h"] >= 4 else c["body_h"] + 24
    if c["gap"] <= -2 and body < 11:
        out.append(SPOTS[0])
    if c["gap"] <= -2 and c["venue_h"] >= 20:
        out.append(SPOTS[1])
    if c["gap"] >= 2 and body >= 22:
        out.append(SPOTS[2])
    if c["gap"] != 0 and body < 12:
        out.append(SPOTS[3])
    return out


def spots_for(g: G, visitor_std: float | None) -> tuple[list, list]:
    """-> (spot bets [(variant, price, result)], every bet [(price, result)]) for game g."""
    tags = spots_in(clock(g, visitor_std))
    spots, every = [], []
    for side in ("away", "home"):
        for kind, price, res in bets(g, side):
            every.append((price, res))
            for t in tags:
                spots.append((f"{kind}: {t}" if side == "away" else f"{kind}: home vs a {t}", price, res))
    for kind, price, res in totals(g):
        every.append((price, res))
        for t in tags:
            spots.append((f"{kind}: {t}", price, res))
    return spots, every


def study(games: list[G], sport: str, recs: dict, control: dict, n_random: int = 100, seed: int = 57) -> None:
    from .nflhist import Record

    rng = random.Random(f"{seed}:{sport}")
    zones: dict[str, Counter] = defaultdict(Counter)

    def put(book, key, season, price, res):
        if price is None or res is None:
            return
        arm = "blind" if season > TUNE_LAST else "in_sample"
        book.setdefault(key, {"in_sample": Record(), "blind": Record()})[arm].add(price, res)

    for g in sorted(games, key=lambda x: (x.start, x.gid)):
        z = zones[g.away]
        visitor = z.most_common(1)[0][0] if sum(z.values()) >= MIN_HOME else None  # earlier games only
        spots, every = spots_for(g, visitor)
        for k in range(n_random):
            if rng.random() < 0.1:
                for price, res in every:
                    put(control, (sport, k), g.season, price, res)
        for variant, price, res in spots:
            put(recs, (sport, variant), g.season, price, res)
        if g.tzo is not None and not g.neutral:
            zones[g.home][standard(g.tzo, g.start.date())] += 1


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


__all__ = ["SPOTS", "clock", "run", "spots_for", "spots_in", "standard", "study", "us_dst"]
