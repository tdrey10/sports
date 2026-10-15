"""Totals after hot or cold scoring, tested blind (study of 2026-10-16).

Idea: after both teams' last games ran well over (or under) their posted totals, or after a
team's last game was a shootout, does the market misprice the next total? Each game gets two
features known before kickoff: the average of the two teams' last-game "total overshoot"
(points scored minus that game's posted total, in percent of the total) and the average of
their last three games' points against the posted total now. Variants bet the over or the
under at the real closing price when the feature is at or beyond a cut (top or bottom 15% of
the tuning seasons). Seasons through 2021 tune, 2022 on are blind. Luck control: random
tenths of the games bet over or under through the same bar.
Data: data/lines/espn/<sport>/<season>.csv.gz.
"""

from __future__ import annotations

import random
from collections import defaultdict, deque
from pathlib import Path

from .linestudies import SPORTS, TUNE_LAST, G, annotate, load, verdict
from .nflhist import Record

CUT = 0.15
STUDY = "16 scoring runs"


def _result(g: G) -> tuple[float, float] | None:
    if not (g.total and g.total_odds["under"] and g.total_odds["over"]):
        return None
    pts = g.hs + g.as_
    under = 1.0 if pts < g.total else 0.5 if pts == g.total else 0.0
    return under, (1.0 - under if under != 0.5 else 0.5)


def features(games: list[G]) -> list[dict | None]:
    """Per game: avg last-game overshoot of the two teams, and avg of their last 3 vs now."""
    last: dict[str, float] = {}
    recent: dict[str, deque] = defaultdict(lambda: deque(maxlen=3))
    season, out = None, []
    for g in games:
        if g.season != season:
            season = g.season
            last.clear()
            recent.clear()
        f = None
        if g.total and g.home in last and g.away in last and len(recent[g.home]) == 3 and len(recent[g.away]) == 3:
            avg3 = (sum(recent[g.home]) + sum(recent[g.away])) / 6  # points per team-game, both sides of those games
            f = {"over_last": (last[g.home] + last[g.away]) / 2, "form": avg3 / g.total - 1}
        out.append(f)
        if g.total:
            pts = g.hs + g.as_
            for t in (g.home, g.away):
                last[t] = pts / g.total - 1
        for t in (g.home, g.away):
            recent[t].append(g.hs + g.as_)
    return out


def _cuts(vals: list[float]) -> tuple[float, float]:
    v = sorted(vals)
    return v[int(len(v) * CUT)], v[int(len(v) * (1 - CUT)) - 1]


def study(games: list[G], sport: str, recs: dict, control: dict, n_random: int = 100, seed: int = 11) -> None:
    feats = features(games)
    tune = [f for g, f in zip(games, feats) if f and g.season <= TUNE_LAST]
    if len(tune) < 200:
        return
    cuts = {k: _cuts([f[k] for f in tune]) for k in ("over_last", "form")}
    rng = random.Random(f"{seed}:{sport}")
    picks = [(rng.random() < 0.5, [rng.random() < 0.1 for _ in games]) for _ in range(n_random)]
    for i, (g, f) in enumerate(zip(games, feats)):
        res = _result(g)
        if not res:
            continue
        under, over = res
        arm = "blind" if g.season > TUNE_LAST else "in_sample"

        def put(book, key, side):
            price = g.total_odds[side]
            book.setdefault(key, {"in_sample": Record(), "blind": Record()})[arm].add(price, under if side == "under" else over)

        for k, (is_over, chosen) in enumerate(picks):
            if chosen[i]:
                put(control, f"random {k}", "over" if is_over else "under")
        if not f:
            continue
        for key, label in (("over_last", "both teams' last games ran over"), ("form", "last 3 games ran over")):
            lo, hi = cuts[key]
            if f[key] >= hi:
                put(recs, (sport, f"{label}: bet the over"), "over")
                put(recs, (sport, f"{label}: bet the under"), "under")
            if f[key] <= lo:
                put(recs, (sport, f"{label.replace('over', 'under')}: bet the over"), "over")
                put(recs, (sport, f"{label.replace('over', 'under')}: bet the under"), "under")


def run(lines_dir: Path, sports=SPORTS, random_n: int = 100) -> dict:
    recs: dict = {}
    control: dict = {}
    for sport in sports:
        folder = Path(lines_dir) / sport
        if not any(folder.glob("*.csv.gz")):
            continue
        games = load(folder, sport)
        annotate(games)
        study(games, sport, recs, control, random_n)
    rows = [{"study": STUDY, "sport": k[0], "variant": k[1], "verdict": verdict(r, k[1]),
             **{a: r[a].to_dict() for a in r}} for k, r in sorted(recs.items())]
    fakes = [verdict(r) for r in control.values()]
    return {"rows": rows, "control": {"spots": len(fakes), "proven": sum(1 for x in fakes if x),
                                      "strong": fakes.count("STRONG")}}
