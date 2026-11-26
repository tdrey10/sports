"""Football turnover and finishing luck: do NFL / college football teams whose turnover margin or
points-per-yard ran hot (or cold) lately regress, and does the closing price chase it?
(study of 2026-11-26)

Idea: turnover margin is mostly noise from week to week (fumble recoveries, tipped picks), and so
is how many points a team squeezes out of its yards, but both swing results a lot and the public
prices records. Per game, from ESPN box scores:
  - turnover luck  = opponent's turnovers - own turnovers;
  - finishing luck = (own points - r * own yards) - (opponent points - r * opponent yards), with r
    the league points per yard from tuning games only.
A side's luck = its average over its last 6 earlier games this season (3+ needed). Spots (top /
bottom 20% of tuning values): lucky side / unlucky side, and an unlucky side facing a lucky one, for
+100..+220 dog ML, -170..-120 favorite ML and the spread side; totals: the matchup's combined
scoring over yards (both sides' own points over yards plus what they allowed over yards) high ->
under, low -> over.
Data: ESPN box scores (data/boxscores/nfl, ncaaf: 2024 season on) joined by game id to ESPN closing
lines (data/lines/espn). Tune on the 2024 season, blind on 2025 and 2026.
Luck control: random tenths of all bets per sport through the same bar.
"""

from __future__ import annotations

import json
import random
from collections import defaultdict, deque
from pathlib import Path

from .linestudies import load, verdict
from .nflhist import Record
from .spotlightstudy import bets, totals

STUDY = "55 football turnover and finishing luck"
SPORTS = ("nfl", "ncaaf")
TUNE_SEASON = 2024
WINDOW, MIN_GAMES = 6, 3
CUT = 0.2
KEYS = ("pts", "yards", "tov")


def load_box(folder: Path) -> dict:
    box = {}
    for f in sorted(Path(folder).glob("*.json")):
        box.update(json.loads(f.read_text()))
    return {k: v for k, v in box.items()
            if all(v.get(s, {}).get(x) is not None for s in ("home", "away") for x in KEYS)}


def ppy(games, box) -> float:
    """League points per yard from tuning-season games only."""
    p = y = 0.0
    for g in games:
        b = box.get(g.gid)
        if b and g.season <= TUNE_SEASON:
            for s in ("home", "away"):
                p, y = p + b[s]["pts"], y + b[s]["yards"]
    return p / y if y else 0.07


def game_luck(own: dict, opp: dict, r: float) -> tuple[float, float, float]:
    """One side's (turnover luck, finishing luck, own scoring over yards) in one game."""
    own_fin = own["pts"] - r * own["yards"]
    opp_fin = opp["pts"] - r * opp["yards"]
    return opp["tov"] - own["tov"], own_fin - opp_fin, own_fin + opp_fin


def features(games, box, r) -> dict:
    """gid -> {side: (turnover luck, finishing luck, scoring over yards) averaged over the team's last
    WINDOW boxed games this season BEFORE this one (None if fewer than MIN_GAMES)}."""
    hist: dict = defaultdict(lambda: deque(maxlen=WINDOW))
    season_of: dict = {}
    out = {}
    for g in sorted(games, key=lambda x: (x.start, x.gid)):
        feat = {}
        for side, team in (("home", g.home), ("away", g.away)):
            if season_of.get(team) != g.season:
                hist[team].clear()
                season_of[team] = g.season
            h = list(hist[team])
            feat[side] = None if len(h) < MIN_GAMES else tuple(sum(x[i] for x in h) / len(h) for i in range(3))
        out[g.gid] = feat
        b = box.get(g.gid)
        if b:  # this game's own box score is used only for LATER games
            hist[g.home].append(game_luck(b["home"], b["away"], r))
            hist[g.away].append(game_luck(b["away"], b["home"], r))
    return out


def _q(vals, q):
    vals = sorted(vals)
    return vals[min(len(vals) - 1, max(0, int(q * len(vals))))] if vals else 0.0


def cutoffs(games, feats) -> dict:
    tov, fin, tot = [], [], []
    for g in games:
        if g.season > TUNE_SEASON:
            continue
        f = feats.get(g.gid, {})
        for s in ("home", "away"):
            if f.get(s):
                tov.append(f[s][0])
                fin.append(f[s][1])
        if f.get("home") and f.get("away"):
            tot.append(f["home"][2] + f["away"][2])
    return {"tov": (_q(tov, CUT), _q(tov, 1 - CUT)), "fin": (_q(fin, CUT), _q(fin, 1 - CUT)),
            "tot": (_q(tot, CUT), _q(tot, 1 - CUT))}


LUCKS = (("tov", 0, "turnover"), ("fin", 1, "finishing"))


def spots_for(g, feat: dict, cut: dict) -> tuple[list, list]:
    spots, every = [], []
    tags = {}
    for key, i, _ in LUCKS:
        lo, hi = cut[key]
        tags[key] = {s: None if not feat.get(s) else "lucky" if feat[s][i] >= hi else "unlucky" if feat[s][i] <= lo
                     else "mid" for s in ("home", "away")}
    for side, other in (("home", "away"), ("away", "home")):
        for kind, price, res in bets(g, side):
            every.append((price, res))
            for key, _, name in LUCKS:
                t = tags[key]
                if t[side] in ("lucky", "unlucky"):
                    spots.append((f"{kind}: {t[side]} {name} side", price, res))
                    if t[side] == "unlucky" and t[other] == "lucky":
                        spots.append((f"{kind}: {name}-unlucky side vs lucky side", price, res))
    for kind, price, res in totals(g):
        every.append((price, res))
        if feat.get("home") and feat.get("away"):
            t = feat["home"][2] + feat["away"][2]
            lo, hi = cut["tot"]
            if (kind == "under" and t >= hi) or (kind == "over" and t <= lo):
                spots.append((f"{kind}: {'hot' if kind == 'under' else 'cold'} points-per-yard matchup", price, res))
    return spots, every


def study(games, box, sport, recs, control, n_random=100, seed=55) -> None:
    games = [g for g in games if g.season >= TUNE_SEASON]
    r = ppy(games, box)
    feats = features(games, box, r)
    cut = cutoffs(games, feats)
    rng = random.Random(f"{seed}:{sport}")

    def put(book, key, season, price, res):
        arm = "blind" if season > TUNE_SEASON else "in_sample"
        book.setdefault(key, {"in_sample": Record(), "blind": Record()})[arm].add(price, res)

    for g in sorted(games, key=lambda x: (x.start, x.gid)):
        spots, every = spots_for(g, feats.get(g.gid, {}), cut)
        for k in range(n_random):
            if rng.random() < 0.1:
                for price, res in every:
                    put(control, (sport, k), g.season, price, res)
        for variant, price, res in spots:
            put(recs, (sport, variant), g.season, price, res)


def run(lines_dir: Path, box_dir: Path, sports=SPORTS, random_n: int = 100) -> dict:
    recs: dict = {}
    control: dict = {}
    for sport in sports:
        folder = Path(lines_dir) / sport
        if not any(folder.glob("*.csv.gz")):
            continue
        study(load(folder, sport), load_box(Path(box_dir) / sport), sport, recs, control, random_n)
    rows = [{"study": STUDY, "sport": k[0], "variant": k[1], "verdict": verdict(r, k[1]),
             **{a: r[a].to_dict() for a in r}} for k, r in sorted(recs.items())]
    fakes = [verdict(r) for r in control.values()]
    return {"rows": rows, "control": {"spots": len(fakes), "proven": sum(1 for x in fakes if x),
                                      "strong": fakes.count("STRONG")}}
