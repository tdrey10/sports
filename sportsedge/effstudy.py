"""Efficiency and tempo (owner, Oct 28: "do all three" - a college hoops efficiency / tempo engine, and
NBA pace for totals).

Two models, both built only from games already played (no peeking), both tested at the closing line:

1. Adjusted offense / defense from the scores, every team, 2016 on. Each team has an offense rating
   (points it scores above an average team) and a defense rating (points it allows above average),
   adjusted for who it played: after each game both teams' ratings move toward what happened, by a
   step tuned on the 2016-2021 seasons for accuracy (never for betting profit). A game's projection:
       home points = league average + home offense + away defense + half the home edge
       away points = league average + away offense + home defense - half the home edge
   The projected margin is compared with the closing spread and the projected total with the closing
   total; when they differ by a threshold, the bet is the side the model likes. Seasons through 2021
   tune, 2022 on are blind, with a luck control of random bets (same bars as every study).

2. Tempo (pace): from ESPN box scores (possessions = FGA - offensive rebounds + turnovers + 0.44 FTA),
   each team's adjusted pace and points per possession, so a projected total = projected possessions x
   both teams' projected efficiency. Box scores start in 2024, so this is tested on the 2024 season
   (tune) and 2025 on (blind): fewer bets, same bars.

Only a variant that passes (PROVEN or STRONG) is looked for on today's games (proven.py).
"""

from __future__ import annotations

import json
import random
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from .linestudies import TUNE_LAST, G, load, verdict
from .nflhist import Record

SPORTS = ("ncaab", "nba", "ncaaf", "nfl")
NAME = {"ncaab": "College hoops", "nba": "NBA", "ncaaf": "College football", "nfl": "NFL"}
SPREAD_PRICE = -110.0
FIRST_BET_SEASON = 2018  # closing lines start 2018
MIN_GAMES = {"ncaab": 6, "nba": 8, "ncaaf": 3, "nfl": 3, "nhl": 8, "mlb": 10}  # games this season before a bet
REGRESS = {"ncaab": 0.6, "nba": 0.7, "ncaaf": 0.6, "nfl": 0.7, "nhl": 0.7, "mlb": 0.7}  # last season's rating kept
STEPS = (0.02, 0.04, 0.06, 0.09, 0.12)  # update size, chosen on the tuning seasons by error
SPREAD_T = {"ncaab": (2, 4, 6, 8), "nba": (1.5, 3, 4.5, 6), "ncaaf": (3, 6, 10, 14), "nfl": (2, 3.5, 5, 7)}
TOTAL_T = {"ncaab": (3, 5, 8, 11), "nba": (2, 4, 6, 8), "ncaaf": (3, 6, 10, 14), "nfl": (2, 4, 6, 8)}
PACE_TUNE_LAST = 2024  # box scores start with the 2024 season


@dataclass
class Ratings:
    """Opponent-adjusted offense / defense in points, updated game by game."""
    sport: str
    k: float
    mu: float = 0.0
    hca: float = 0.0
    off: dict = field(default_factory=lambda: defaultdict(float))
    dfn: dict = field(default_factory=lambda: defaultdict(float))
    games: dict = field(default_factory=lambda: defaultdict(int))
    season: int | None = None

    def new_season(self, season: int) -> None:
        if season != self.season:
            keep = REGRESS[self.sport]
            for d in (self.off, self.dfn):
                for t in d:
                    d[t] *= keep
            self.games = defaultdict(int)
            self.season = season

    def project(self, home: str, away: str, neutral: bool) -> tuple[float, float]:
        h = 0.0 if neutral else self.hca / 2
        return (self.mu + self.off[home] + self.dfn[away] + h, self.mu + self.off[away] + self.dfn[home] - h)

    def ready(self, home: str, away: str) -> bool:
        need = MIN_GAMES[self.sport]
        return self.games[home] >= need and self.games[away] >= need

    def add(self, g: G) -> tuple[float, float]:
        """Learn from a final; returns the projection made before it."""
        if self.mu == 0.0:
            self.mu = (g.hs + g.as_) / 2
        ph, pa = self.project(g.home, g.away, g.neutral)
        eh, ea = g.hs - ph, g.as_ - pa
        self.off[g.home] += self.k * eh
        self.dfn[g.away] += self.k * eh
        self.off[g.away] += self.k * ea
        self.dfn[g.home] += self.k * ea
        self.mu += 0.01 * (eh + ea) / 2
        if not g.neutral:
            self.hca += 0.01 * ((g.hs - g.as_) - (ph - pa))
        self.games[g.home] += 1
        self.games[g.away] += 1
        return ph, pa


def _possessions(t: dict) -> float | None:
    try:
        return t["fga"] - t["oreb"] + t["tov"] + 0.44 * t["fta"]
    except (KeyError, TypeError):
        return None


def load_boxes(root, sport: str) -> dict[str, dict]:
    out = {}
    for f in sorted((Path(root) / "boxscores" / sport).glob("*.json")):
        try:
            out.update(json.loads(f.read_text()))
        except (OSError, ValueError):
            continue
    return out


@dataclass
class Pace:
    """Adjusted possessions per game and points per possession, from box scores."""
    sport: str
    k: float = 0.06
    lg_pace: float = 0.0
    lg_ppp: float = 0.0
    pace: dict = field(default_factory=lambda: defaultdict(float))
    off: dict = field(default_factory=lambda: defaultdict(float))
    dfn: dict = field(default_factory=lambda: defaultdict(float))
    games: dict = field(default_factory=lambda: defaultdict(int))
    season: int | None = None

    def new_season(self, season: int) -> None:
        if season != self.season:
            keep = REGRESS[self.sport]
            for d in (self.pace, self.off, self.dfn):
                for t in d:
                    d[t] *= keep
            self.games = defaultdict(int)
            self.season = season

    def ready(self, home: str, away: str) -> bool:
        need = MIN_GAMES[self.sport]
        return self.lg_pace > 0 and self.games[home] >= need and self.games[away] >= need

    def total(self, home: str, away: str) -> float:
        poss = self.lg_pace + self.pace[home] + self.pace[away]
        return poss * (2 * self.lg_ppp + self.off[home] + self.dfn[away] + self.off[away] + self.dfn[home])

    def add(self, home: str, away: str, box: dict) -> None:
        ph, pa = _possessions(box.get("home") or {}), _possessions(box.get("away") or {})
        if not ph or not pa:
            return
        poss = (ph + pa) / 2
        hp, ap = (box["home"].get("pts") or 0) / poss, (box["away"].get("pts") or 0) / poss
        if self.lg_pace == 0:
            self.lg_pace, self.lg_ppp = poss, (hp + ap) / 2
        e = poss - (self.lg_pace + self.pace[home] + self.pace[away])
        self.pace[home] += self.k * e / 2
        self.pace[away] += self.k * e / 2
        for team, opp, ppp in ((home, away, hp), (away, home, ap)):
            err = ppp - (self.lg_ppp + self.off[team] + self.dfn[opp])
            self.off[team] += self.k * err
            self.dfn[opp] += self.k * err
        self.lg_pace += 0.01 * e
        self.lg_ppp += 0.01 * ((hp + ap) / 2 - self.lg_ppp)
        self.games[home] += 1
        self.games[away] += 1


def _games(games: list[G]) -> list[G]:
    return sorted((g for g in games if g.stype != "3"), key=lambda g: g.start)


def fit_k(games: list[G], sport: str) -> float:
    """The update step with the smallest margin and total error on the tuning seasons."""
    best = None
    for k in STEPS:
        r, err, n = Ratings(sport, k), 0.0, 0
        for g in games:
            if g.season > TUNE_LAST:
                break
            r.new_season(g.season)
            ready = r.ready(g.home, g.away)
            ph, pa = r.add(g)
            if ready:
                err += ((g.hs - g.as_) - (ph - pa)) ** 2 + ((g.hs + g.as_) - (ph + pa)) ** 2
                n += 1
        if n and (best is None or err / n < best[1]):
            best = (k, err / n)
    return best[0] if best else STEPS[2]


def _result(g: G, market: str, side: str) -> tuple[float, float] | None:
    """(price, result) for a spread side or a total side at the close."""
    if market == "spread":
        if g.spread is None:
            return None
        m = (g.margin + g.spread) if side == "home" else -(g.margin + g.spread)
        return g.spread_odds.get(side) or SPREAD_PRICE, 1.0 if m > 0 else 0.0 if m < 0 else 0.5
    if not g.total or not g.total_odds.get(side):
        return None
    pts = g.hs + g.as_
    over = 1.0 if pts > g.total else 0.0 if pts < g.total else 0.5
    return g.total_odds[side], over if side == "over" else 1.0 - over


def edges(sport: str, ph: float, pa: float, spread: float | None, total: float | None) -> list[tuple[str, str, str]]:
    """(market, side, variant) for every threshold the projection clears against today's (or the closing) line."""
    out = []
    if spread is not None:
        e = (ph - pa) + spread  # > 0: the home side is better than its line
        for t in SPREAD_T[sport]:
            if abs(e) >= t:
                out.append(("spread", "home" if e > 0 else "away", f"ratings: spread {t:g}+ pts off the line"))
    if total:
        e = (ph + pa) - total
        for t in TOTAL_T[sport]:
            if abs(e) >= t:
                side = "over" if e > 0 else "under"
                out.append((side, side, f"ratings: total {t:g}+ pts {side} the line"))
    return out


def pace_edges(sport: str, proj: float, total: float | None) -> list[tuple[str, str, str]]:
    if not total:
        return []
    e = proj - total
    side = "over" if e > 0 else "under"
    return [(side, side, f"pace: total {t:g}+ pts {side} the line") for t in TOTAL_T[sport] if abs(e) >= t]


def study(games: list[G], sport: str, boxes: dict | None = None, n_random: int = 100, seed: int = 28) -> dict:
    games = _games(games)
    k = fit_k(games, sport)
    r, p = Ratings(sport, k), Pace(sport)
    recs: dict = {}
    control: dict = {}
    rng = random.Random(seed)
    boxes = boxes or {}

    def put(book, key, g, price, result, tune_last=TUNE_LAST):
        arm = "blind" if g.season > tune_last else "in_sample"
        book.setdefault(key, {"in_sample": Record(), "blind": Record()})[arm].add(price, result)

    for g in games:
        r.new_season(g.season)
        p.new_season(g.season)
        ready = r.ready(g.home, g.away)
        ph, pa = r.project(g.home, g.away, g.neutral)
        pace_ok = p.ready(g.home, g.away)
        pace_total = p.total(g.home, g.away) if pace_ok else None
        if g.season >= FIRST_BET_SEASON:
            if ready:
                for market, side, variant in edges(sport, ph, pa, g.spread, g.total):
                    res = _result(g, "spread" if market == "spread" else market, side)
                    if res:
                        put(recs, (market, variant), g, *res)
                for k_ in range(n_random):  # luck control: random sides on random tenths of games
                    if rng.random() < 0.1:
                        market = rng.choice(("spread", "total"))
                        side = rng.choice(("home", "away")) if market == "spread" else rng.choice(("over", "under"))
                        res = _result(g, market, side)
                        if res:
                            put(control, (market, k_), g, *res)
            if pace_total is not None:
                for market, side, variant in pace_edges(sport, pace_total, g.total):
                    res = _result(g, market, side)
                    if res:
                        put(recs, (market, variant), g, *res, tune_last=PACE_TUNE_LAST)
        r.add(g)
        box = boxes.get(g.gid)
        if box:
            p.add(g.home, g.away, box)
    rows = [{"study": f"{NAME[sport]} efficiency and tempo", "sport": sport, "market": key[0], "variant": key[1],
             "verdict": verdict(rec, key[1]), **{a: rec[a].to_dict() for a in rec}} for key, rec in sorted(recs.items())]
    fakes = [verdict(rec) for rec in control.values()]
    return {"k": k, "rows": rows,
            "control": {"spots": len(fakes), "proven": sum(1 for x in fakes if x), "strong": fakes.count("STRONG")}}


def run_all(root="data", sports=SPORTS) -> dict:
    root = Path(root)
    out = {}
    for s in sports:
        folder = root / "lines" / "espn" / s
        if any(folder.glob("*.csv.gz")):
            out[s] = study(load(folder, s), s, load_boxes(root, s))
    return out


def report(res: dict) -> str:
    lines = []
    for s, r in res.items():
        c = r["control"]
        lines.append(f"{NAME[s]} efficiency and tempo (step {r['k']}): luck control {c['proven']}/{c['spots']} random spots passed.")
        for row in r["rows"]:
            t, b = row["in_sample"], row["blind"]
            f = lambda x: "  -  " if x["flat_roi"] is None else f"{x['flat_roi']:+.1%}"  # noqa: E731
            lines.append(f"  {row['market']:<7} {row['variant']:<38} tune {t['bets']:>5} {f(t)}  blind {b['bets']:>5} {f(b)} {row['verdict']}")
    return "\n".join(lines)


def current(sport: str, root="data") -> tuple[Ratings, Pace, dict[str, str]]:
    """Ratings and pace as of the last final we hold, and display name -> archive team id."""
    from . import factorstudy

    games, names = factorstudy.history(sport, root)
    games = _games(games)
    r, p = Ratings(sport, fit_k(games, sport)), Pace(sport)
    boxes = load_boxes(root, sport)
    for g in games:
        r.new_season(g.season)
        p.new_season(g.season)
        r.add(g)
        if g.gid in boxes:
            p.add(g.home, g.away, boxes[g.gid])
    return r, p, names


__all__ = ["Pace", "Ratings", "current", "edges", "pace_edges", "report", "run_all", "study"]
