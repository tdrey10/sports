"""Season over rates and rematch totals, tested blind (study of 2026-10-30).

Idea: does the posted total lag a team's habit? Over/under when both teams' games so far this
season went over their posted totals at 60%+ (or 40%- ), and in rematches when the last meeting
this season went over or under. Only earlier games count (min MIN_GAMES per team).
Closing totals 2018+: seasons through TUNE_LAST tune, later ones are blind.
Luck control: random tenths of all totals (over and under) through the same bar.
Data: data/lines/espn/<sport>/<season>.csv.gz.
"""

from __future__ import annotations

import random
from pathlib import Path

from .linestudies import SPORTS, TUNE_LAST, G, load, verdict
from .nflhist import Record
from .reststudy import result

STUDY = "26 season over rates and rematch totals"
MIN_GAMES = {"nfl": 6, "ncaaf": 6, "nba": 12, "ncaab": 12, "nhl": 12, "mlb": 20}
HIGH, LOW = 0.6, 0.4


def history(games: list[G]) -> list[dict]:
    """Per game: each team's over rate so far this season (None until MIN_GAMES) and the last
    meeting's over result (1 over, 0 under, None if none or a push), from earlier games only."""
    out = []
    season = None
    tally: dict[str, list[float]] = {}
    meet: dict[frozenset, float | None] = {}
    for g in games:
        if g.season != season:
            season = g.season
            tally.clear()
            meet.clear()
        n = MIN_GAMES[g.sport]
        rate = lambda t: (sum(tally[t]) / len(tally[t])) if len(tally.get(t, [])) >= n else None  # noqa: E731
        out.append({"home": rate(g.home), "away": rate(g.away), "meet": meet.get(frozenset((g.home, g.away)))})
        res = result(g)
        if res is not None:
            over = res[1]
            for t in (g.home, g.away):
                tally.setdefault(t, []).append(over)
            meet[frozenset((g.home, g.away))] = None if over == 0.5 else over
    return out


def spots(h: dict) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    a, b = h["home"], h["away"]
    if a is not None and b is not None:
        if a >= HIGH and b >= HIGH:
            out += [("over: both teams' games run over", "over"), ("under: both teams' games run over", "under")]
        if a <= LOW and b <= LOW:
            out += [("over: both teams' games run under", "over"), ("under: both teams' games run under", "under")]
    if h["meet"] == 1.0:
        out += [("over: last meeting went over", "over"), ("under: last meeting went over", "under")]
    if h["meet"] == 0.0:
        out += [("over: last meeting went under", "over"), ("under: last meeting went under", "under")]
    return out


def study(games: list[G], sport: str, recs: dict, control: dict, n_random: int = 100, seed: int = 30) -> None:
    rng = random.Random(f"{seed}:{sport}")
    hist = history(games)

    def put(book, key, g, price, res):
        arm = "blind" if g.season > TUNE_LAST else "in_sample"
        book.setdefault(key, {"in_sample": Record(), "blind": Record()})[arm].add(price, res)

    for g, h in zip(games, hist):
        res = result(g)
        picks = [rng.random() < 0.1 for _ in range(n_random)]
        if res is None:
            continue
        under, over = res
        for k, hit in enumerate(picks):
            if hit:
                side = "under" if rng.random() < 0.5 else "over"
                put(control, f"random {k}", g, g.total_odds[side], under if side == "under" else over)
        for label, side in spots(h):
            put(recs, (sport, label), g, g.total_odds[side], under if side == "under" else over)


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
