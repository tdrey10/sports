"""Pace-implied totals vs the posted total, tested blind (study of 2026-11-06).

Idea: build a crude projected total from each team's season-to-date scoring: the home side's
points scored per game blended with the away side's points allowed per game, plus the reverse
(all from earlier games only, reset every season, min MIN_GAMES each). gap = projection minus
the posted closing total. Over when the gap is at least +EDGE (market too low), under when it is
at most -EDGE (market too high). Bets at the real closing total prices.
Closing lines 2018+: seasons through TUNE_LAST tune, later ones are blind.
Luck control: random tenths of all totals (over and under) through the same bar.
Data: data/lines/espn/<sport>/<season>.csv.gz.
"""

from __future__ import annotations

import random
from pathlib import Path

from .linestudies import SPORTS, TUNE_LAST, G, load, verdict
from .nflhist import Record
from .reststudy import result

STUDY = "32 projected total from season scoring vs posted total"
MIN_GAMES = {"nfl": 6, "ncaaf": 6, "nba": 15, "ncaab": 12, "nhl": 16, "mlb": 30}
EDGE = {"nfl": 3.0, "ncaaf": 5.0, "nba": 6.0, "ncaab": 6.0, "nhl": 0.6, "mlb": 1.0}


def history(games: list[G]) -> list[float | None]:
    """Projected total per game from earlier games only (None until both sides have enough)."""
    out: list[float | None] = []
    season = None
    st: dict = {}  # team -> [points for, points against, games]
    for g in games:
        if g.season != season:
            season = g.season
            st.clear()
        h, a = st.get(g.home), st.get(g.away)
        n = MIN_GAMES[g.sport]
        if h and a and h[2] >= n and a[2] >= n:
            out.append((h[0] / h[2] + a[1] / a[2] + a[0] / a[2] + h[1] / h[2]) / 2)
        else:
            out.append(None)
        for t, pf, pa in ((g.home, g.hs, g.as_), (g.away, g.as_, g.hs)):
            d = st.setdefault(t, [0.0, 0.0, 0])
            d[0] += pf
            d[1] += pa
            d[2] += 1
    return out


def spots(g: G, proj: float | None) -> list[tuple[str, str]]:
    if proj is None or not g.total:
        return []
    gap = proj - g.total
    t = EDGE[g.sport]
    out = []
    if gap >= t:
        out.append(("over: projection above the posted total", "over"))
    if gap <= -t:
        out.append(("under: projection below the posted total", "under"))
    if gap >= t:
        out.append(("under: projection above the posted total (fade)", "under"))
    if gap <= -t:
        out.append(("over: projection below the posted total (fade)", "over"))
    return out


def study(games: list[G], sport: str, recs: dict, control: dict, n_random: int = 100, seed: int = 36) -> None:
    rng = random.Random(f"{seed}:{sport}")
    hist = history(games)

    def put(book, key, g, price, res):
        arm = "blind" if g.season > TUNE_LAST else "in_sample"
        book.setdefault(key, {"in_sample": Record(), "blind": Record()})[arm].add(price, res)

    for g, proj in zip(games, hist):
        res = result(g)
        picks = [rng.random() < 0.1 for _ in range(n_random)]
        if res is None:
            continue
        under, over = res
        for k, hit in enumerate(picks):
            if hit:
                side = "under" if rng.random() < 0.5 else "over"
                put(control, f"random {k}", g, g.total_odds[side], under if side == "under" else over)
        for label, side in spots(g, proj):
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
