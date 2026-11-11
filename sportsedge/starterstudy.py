"""Starter workload and familiarity: MLB pitchers and NHL goalies (study of 2026-11-11).

Idea: the market prices WHO starts (backups, last outing: line studies family 12), but maybe not
HOW the starter got here: an NHL goalie starting on back-to-back days or after a long layoff, an
MLB starter on short rest or back from a long gap (injured list, skipped turn), or a starter who
faced this same lineup within the last 14 days (familiarity: hitters/shooters just saw him).
Tested for the side with that starter and against it, on +100..+220 moneyline dogs, -170..-120
favorites and the run/puck line, plus game overs and unders.
Uses earlier games only; each starter's history resets each season. Closing lines 2018+: seasons
through TUNE_LAST tune, later ones are blind. Luck control: random tenths of all bets through the
same bar. Data: data/lines/espn/<sport>/<season>.csv.gz (sp_home / sp_away).
"""

from __future__ import annotations

import random
from pathlib import Path

from .atsstudy import cover_margin
from .linestudies import SMALL_DOG, TUNE_LAST, G, _dog, load, verdict
from .nflhist import Record

STUDY = "36 starter workload and familiarity"
SPORTS = ("mlb", "nhl")
REMATCH_DAYS = 14
# (short rest at most, long layoff at least), in days since the starter's last start this season.
REST = {"mlb": (4.0, 10.0), "nhl": (1.5, 8.0)}
SHORT = {"mlb": "starter on short rest (4 days or less)", "nhl": "goalie on back-to-back days"}
LONG = {"mlb": "starter back from a 10+ day gap", "nhl": "goalie back from 8+ days off"}
REMATCH = "starter faced this opponent in the last 14 days"


def starter_context(games: list[G]) -> list[dict]:
    """Each game's side -> list of conditions its starter meets, from earlier games only.
    A starter's history is keyed by (team, name) and resets each season."""
    last: dict[tuple, tuple] = {}  # (team, starter) -> (start time, opponent)
    met: dict[tuple, object] = {}  # (team, starter, opponent) -> last start time vs that opponent
    season = None
    out = []
    for g in games:
        if g.season != season:
            season = g.season
            last.clear()
            met.clear()
        short, long_ = REST.get(g.sport, REST["mlb"])
        row = {}
        for side, team, opp in (("home", g.home, g.away), ("away", g.away, g.home)):
            name = g.starter.get(side) or ""
            conds: list[str] = []
            if name:
                prev = last.get((team, name))
                if prev:
                    days = (g.start - prev[0]).total_seconds() / 86400
                    if 0 < days <= short:
                        conds.append(SHORT.get(g.sport, SHORT["mlb"]))
                    elif days >= long_:
                        conds.append(LONG.get(g.sport, LONG["mlb"]))
                seen = met.get((team, name, opp))
                if seen and 0 < (g.start - seen).total_seconds() / 86400 <= REMATCH_DAYS:
                    conds.append(REMATCH)
            row[side] = conds
        out.append(row)
        for side, team, opp in (("home", g.home, g.away), ("away", g.away, g.home)):
            name = g.starter.get(side) or ""
            if name:
                last[(team, name)] = (g.start, opp)
                met[(team, name, opp)] = g.start
    return out


def spots(ctx: dict, side: str) -> list[str]:
    other = "away" if side == "home" else "home"
    return [f"own {c}" for c in ctx.get(side, [])] + [f"opponent {c}" for c in ctx.get(other, [])]


def study(games: list[G], sport: str, recs: dict, control: dict, n_random: int = 100, seed: int = 36) -> None:
    ctxs = starter_context(games)
    rng = random.Random(f"{seed}:{sport}")
    chosen = [[rng.random() < 0.1 for _ in games] for _ in range(n_random)]

    def put(book, key, g, price, res):
        arm = "blind" if g.season > TUNE_LAST else "in_sample"
        book.setdefault(key, {"in_sample": Record(), "blind": Record()})[arm].add(price, res)

    for i, g in enumerate(games):
        bets_all = []
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
                kinds.append(("run/puck line", sp, 1.0 if m > 0 else 0.0 if m < 0 else 0.5))
            bets_all += kinds
            for s in spots(ctxs[i], side):
                for kind, price, res in kinds:
                    put(recs, (sport, f"{kind}: {s}"), g, price, res)
        totals = []
        if g.total is not None:
            pts = g.hs + g.as_
            for ou in ("over", "under"):
                price = g.total_odds.get(ou)
                if price:
                    d = (pts - g.total) if ou == "over" else (g.total - pts)
                    totals.append((ou, price, 1.0 if d > 0 else 0.0 if d < 0 else 0.5))
        conds = sorted(set(ctxs[i]["home"]) | set(ctxs[i]["away"]))
        for c in conds:
            for ou, price, res in totals:
                put(recs, (sport, f"{ou}: a {c}"), g, price, res)
        for k in range(n_random):
            if chosen[k][i]:
                for _, price, res in bets_all + totals:
                    put(control, f"random {k}", g, price, res)


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
