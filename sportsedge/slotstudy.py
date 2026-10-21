"""Dogs and favorites by kickoff slot, tested blind (study of 2026-10-21).

Idea: does the time slot (weekday, day or night start in Eastern time) change who the price
shades? NFL Thursday / Monday / Sunday early, late and night, college football Friday / Saturday
day and night, daytime baseball, weekend and matinee hockey and hoops. Tested for dogs
(+100 to +220 closing) and lock-price favorites (-170 to -120).
Closing lines 2018+: seasons through TUNE_LAST tune, later ones are blind.
Luck control: random tenths of all dogs and all lock favorites through the same bar.
Data: data/lines/espn/<sport>/<season>.csv.gz.
"""

from __future__ import annotations

import random
from pathlib import Path
from zoneinfo import ZoneInfo

from .linestudies import SMALL_DOG, SPORTS, TUNE_LAST, G, _dog, load, verdict
from .nflhist import Record

STUDY = "20 kickoff slot"
EASTERN = ZoneInfo("America/New_York")
DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def is_lock_price(price) -> bool:
    return price is not None and -170 <= price <= -120


def slots(g: G) -> list[str]:
    """Slot labels for a game: weekday plus day or night in Eastern time."""
    t = g.start.astimezone(EASTERN)
    day = DAYS[t.weekday()]
    night = t.hour >= 19
    out = [day, f"{day} {'night' if night else 'day'}"]
    if g.sport == "nfl" and t.weekday() == 6:
        out.append("Sun early" if t.hour < 15 else "Sun late" if t.hour < 19 else "Sun night")
    if g.sport in ("mlb", "nba", "nhl", "ncaab"):
        out.append("weekend" if t.weekday() >= 5 else "weekday")
        if t.hour < 17:
            out.append("matinee")
    return out


def spots(g: G, side: str) -> list[str]:
    p = g.ml.get(side)
    if p is None:
        return []
    kind = "dog" if _dog(p, *SMALL_DOG) else "lock favorite" if is_lock_price(p) else ""
    return [f"{kind}: {s}" for s in slots(g)] if kind else []


def study(games: list[G], sport: str, recs: dict, control: dict, n_random: int = 100, seed: int = 21) -> None:
    rng = random.Random(f"{seed}:{sport}")
    chosen = [[rng.random() < 0.1 for _ in games] for _ in range(n_random)]

    def put(book, key, g, side):
        arm = "blind" if g.season > TUNE_LAST else "in_sample"
        book.setdefault(key, {"in_sample": Record(), "blind": Record()})[arm].add(g.ml[side], g.won(side))

    for i, g in enumerate(games):
        for side in ("home", "away"):
            labels = spots(g, side)
            if not labels:
                continue
            for k in range(n_random):
                if chosen[k][i]:
                    put(control, f"random {k}", g, side)
            for s in labels:
                put(recs, (sport, s), g, side)


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
