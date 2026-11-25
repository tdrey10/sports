"""Basketball shooting luck: do teams whose three-point and free-throw shooting ran hot (or cold)
lately regress, and does the market over-price the hot streak? (study of 2026-11-25)

Idea: three-point percentage (and what opponents shoot from three against you) is mostly noise
over a handful of games, but it swings scores and results a lot, and the price may chase it.
"Shooting luck" per game = 3 * (threes made - p3 * threes tried) + (free throws made - pft *
free throws tried), with p3 / pft the league rates from tuning games only. A side's net luck =
its own shooting luck minus its opponents' shooting luck against it, averaged over its last 10
earlier games this season (5+ needed). Spots (top / bottom 20% of tuning values):
  - lucky side / unlucky side, and an unlucky side facing a lucky one,
  for +100..+220 dog ML, -170..-120 favorite ML and the spread side;
  - totals: the game's "scoring luck" = both sides' own luck plus the luck their opponents had
    against them; high -> under, low -> over.
Data: ESPN box scores (data/boxscores/nba, ncaab: 2024-25 season on) joined by game id to ESPN
closing lines (data/lines/espn). Tune on the 2024 season, blind on 2025 and 2026.
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

STUDY = "54 basketball shooting luck"
SPORTS = ("nba", "ncaab")
TUNE_SEASON = 2024
WINDOW, MIN_GAMES = 10, 5
CUT = 0.2


def load_box(folder: Path) -> dict:
    box = {}
    for f in sorted(Path(folder).glob("*.json")):
        box.update(json.loads(f.read_text()))
    return {k: v for k, v in box.items()
            if all(v.get(s, {}).get(x) is not None for s in ("home", "away") for x in ("tpm", "tpa", "ftm", "fta"))}


def rates(games, box) -> tuple[float, float]:
    """League three-point and free-throw rates from tuning-season games only."""
    tm = ta = fm = fa = 0.0
    for g in games:
        b = box.get(g.gid)
        if b and g.season <= TUNE_SEASON:
            for s in ("home", "away"):
                tm, ta, fm, fa = tm + b[s]["tpm"], ta + b[s]["tpa"], fm + b[s]["ftm"], fa + b[s]["fta"]
    return (tm / ta if ta else 0.35), (fm / fa if fa else 0.72)


def luck(stat: dict, p3: float, pft: float) -> float:
    """Points one side's shooting ran above (or below) league rates in one game."""
    return 3 * (stat["tpm"] - p3 * stat["tpa"]) + (stat["ftm"] - pft * stat["fta"])


def features(games, box, p3, pft) -> dict:
    """gid -> {side: (net luck, scoring luck)} from each team's last WINDOW boxed games this
    season BEFORE this one (None if fewer than MIN_GAMES)."""
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
            if len(h) < MIN_GAMES:
                feat[side] = None
            else:
                n = len(h)
                feat[side] = (sum(a - b for a, b in h) / n, sum(a + b for a, b in h) / n)
        out[g.gid] = feat
        b = box.get(g.gid)
        if b:  # this game's own shooting is used only for LATER games
            lh, la = luck(b["home"], p3, pft), luck(b["away"], p3, pft)
            hist[g.home].append((lh, la))
            hist[g.away].append((la, lh))
    return out


def _q(vals, q):
    vals = sorted(vals)
    return vals[min(len(vals) - 1, max(0, int(q * len(vals))))] if vals else 0.0


def cutoffs(games, feats) -> dict:
    net, tot = [], []
    for g in games:
        if g.season > TUNE_SEASON:
            continue
        f = feats.get(g.gid, {})
        for s in ("home", "away"):
            if f.get(s):
                net.append(f[s][0])
        if f.get("home") and f.get("away"):
            tot.append(f["home"][1] + f["away"][1])
    return {"lo": _q(net, CUT), "hi": _q(net, 1 - CUT), "tot_lo": _q(tot, CUT), "tot_hi": _q(tot, 1 - CUT)}


def spots_for(g, feat: dict, cut: dict) -> tuple[list, list]:
    spots, every = [], []
    tag = {}
    for s in ("home", "away"):
        f = feat.get(s)
        tag[s] = None if not f else "lucky" if f[0] >= cut["hi"] else "unlucky" if f[0] <= cut["lo"] else "mid"
    for side, other in (("home", "away"), ("away", "home")):
        for kind, price, res in bets(g, side):
            every.append((price, res))
            if tag[side] in ("lucky", "unlucky"):
                spots.append((f"{kind}: {tag[side]} shooting side", price, res))
                if tag[side] == "unlucky" and tag[other] == "lucky":
                    spots.append((f"{kind}: unlucky side vs lucky side", price, res))
    for kind, price, res in totals(g):
        every.append((price, res))
        if feat.get("home") and feat.get("away"):
            t = feat["home"][1] + feat["away"][1]
            if (kind == "under" and t >= cut["tot_hi"]) or (kind == "over" and t <= cut["tot_lo"]):
                spots.append((f"{kind}: {'hot' if kind == 'under' else 'cold'}-shooting matchup", price, res))
    return spots, every


def study(games, box, sport, recs, control, n_random=100, seed=54) -> None:
    games = [g for g in games if g.season >= TUNE_SEASON]
    p3, pft = rates(games, box)
    feats = features(games, box, p3, pft)
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
