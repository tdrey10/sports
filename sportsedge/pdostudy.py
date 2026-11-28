"""Hockey PDO luck: do NHL teams whose shooting and save percentages ran hot (or cold) lately
regress, and does the market over-price the streak? (study of 2026-11-28)

Idea: PDO (shooting % + save %) is mostly noise over a handful of games, but it swings goals
and results a lot, and the price may chase it. First use of the ESPN NHL box scores (shots,
power plays). Two kinds of luck per game, with league rates from tuning games only:
  - finishing luck = goals - sh * shots (sh = league goals per shot); a side's net finishing
    luck = its own minus what opponents finished against it (PDO in goals);
  - power-play luck = power-play goals - pp * power-play chances (pp = league conversion);
    net = own minus opponents' against it.
A side's value = its average over its last 10 earlier boxed games this season (5+ needed).
Spots (top / bottom 20% of tuning values): lucky side / unlucky side, and an unlucky side
facing a lucky one, for +100..+220 dog ML and -170..-120 favorite ML; totals: the game's
finishing luck (both sides' own luck plus their opponents' against them) high -> under,
low -> over. All at the real closing price.
Data: data/boxscores/nhl (2024-25 season on) joined by ESPN game id to data/lines/espn/nhl.
Tune on the 2024 season, blind on 2025 and 2026. Luck control: random tenths of all bets.
"""

from __future__ import annotations

import json
import random
from collections import defaultdict, deque
from pathlib import Path

from .linestudies import load, verdict
from .nflhist import Record
from .spotlightstudy import bets, totals

STUDY = "58 hockey PDO luck"
SPORTS = ("nhl",)
TUNE_SEASON = 2024
WINDOW, MIN_GAMES = 10, 5
CUT = 0.2
KINDS = ("finishing", "power-play")


def load_box(folder: Path) -> dict:
    box = {}
    for f in sorted(Path(folder).glob("*.json")):
        box.update(json.loads(f.read_text()))
    return {k: v for k, v in box.items()
            if all(v.get(s, {}).get(x) is not None for s in ("home", "away") for x in ("pts", "shots", "ppg", "ppo"))
            and v["home"]["shots"] > 0 and v["away"]["shots"] > 0}


def rates(games, box) -> tuple[float, float]:
    """League goals per shot and power-play conversion from tuning-season games only."""
    g_ = sh = pg = po = 0.0
    for g in games:
        b = box.get(g.gid)
        if b and g.season <= TUNE_SEASON:
            for s in ("home", "away"):
                g_, sh, pg, po = g_ + b[s]["pts"], sh + b[s]["shots"], pg + b[s]["ppg"], po + b[s]["ppo"]
    return (g_ / sh if sh else 0.1), (pg / po if po else 0.2)


def luck(stat: dict, sh: float, pp: float) -> tuple[float, float]:
    """(finishing luck, power-play luck) in goals for one side in one game."""
    return stat["pts"] - sh * stat["shots"], stat["ppg"] - pp * stat["ppo"]


def features(games, box, sh, pp) -> dict:
    """gid -> {side: (net finishing, net power-play, scoring luck)} from each team's last
    WINDOW boxed games this season BEFORE this one (None if fewer than MIN_GAMES)."""
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
                feat[side] = (sum(o[0] - a[0] for o, a in h) / n, sum(o[1] - a[1] for o, a in h) / n,
                              sum(o[0] + a[0] for o, a in h) / n)
        out[g.gid] = feat
        b = box.get(g.gid)
        if b:  # this game's own box score is used only for LATER games
            lh, la = luck(b["home"], sh, pp), luck(b["away"], sh, pp)
            hist[g.home].append((lh, la))
            hist[g.away].append((la, lh))
    return out


def _q(vals, q):
    vals = sorted(vals)
    return vals[min(len(vals) - 1, max(0, int(q * len(vals))))] if vals else 0.0


def cutoffs(games, feats) -> dict:
    vals = {k: [] for k in KINDS}
    tot = []
    for g in games:
        if g.season > TUNE_SEASON:
            continue
        f = feats.get(g.gid, {})
        for s in ("home", "away"):
            if f.get(s):
                for i, k in enumerate(KINDS):
                    vals[k].append(f[s][i])
        if f.get("home") and f.get("away"):
            tot.append(f["home"][2] + f["away"][2])
    cut = {k: (_q(v, CUT), _q(v, 1 - CUT)) for k, v in vals.items()}
    cut["total"] = (_q(tot, CUT), _q(tot, 1 - CUT))
    return cut


def spots_for(g, feat: dict, cut: dict) -> tuple[list, list]:
    spots, every = [], []
    tags = {}
    for i, k in enumerate(KINDS):
        lo, hi = cut[k]
        for s in ("home", "away"):
            f = feat.get(s)
            tags[(k, s)] = None if not f else "lucky" if f[i] >= hi else "unlucky" if f[i] <= lo else "mid"
    for side, other in (("home", "away"), ("away", "home")):
        for kind, price, res in bets(g, side):
            every.append((price, res))
            for k in KINDS:
                t = tags[(k, side)]
                if t in ("lucky", "unlucky"):
                    spots.append((f"{kind}: {t} {k} side", price, res))
                    if t == "unlucky" and tags[(k, other)] == "lucky":
                        spots.append((f"{kind}: {k} unlucky side vs lucky side", price, res))
    lo, hi = cut["total"]
    for kind, price, res in totals(g):
        every.append((price, res))
        if feat.get("home") and feat.get("away"):
            t = feat["home"][2] + feat["away"][2]
            if (kind == "under" and t >= hi) or (kind == "over" and t <= lo):
                spots.append((f"{kind}: {'hot' if kind == 'under' else 'cold'}-finishing matchup", price, res))
    return spots, every


def study(games, box, sport, recs, control, n_random=100, seed=56) -> None:
    games = [g for g in games if g.season >= TUNE_SEASON]
    sh, pp = rates(games, box)
    feats = features(games, box, sh, pp)
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


# Watch item (study of 2026-11-28): the one variant that passed the bar. Not wired, no units: its blind
# z (1.26 on the first run) is under the engine's luck guard (proven.MIN_Z). Re-tested every morning,
# unchanged, so we see if it holds up as this season's games come in. Not to be tweaked.
WATCH = ("nhl", "dog ML: unlucky finishing side")


def watch(res: dict) -> dict | None:
    """The watch spot's row plus its blind z and whether it would clear the luck guard."""
    from .proven import MIN_Z

    for r in res.get("rows", []):
        if (r["sport"], r["variant"]) == WATCH:
            b = r["blind"]
            z = b["flat_roi"] / b["se"] if b.get("flat_roi") is not None and b.get("se") else None
            return {**r, "blind_z": z, "clears_luck_guard": bool(r["verdict"] and z is not None and z >= MIN_Z)}
    return None


def report(res: dict) -> str:
    lines = [f"Hockey PDO luck (blind 2025+): {len(res['rows'])} variants, "
             f"luck control {res['control']['proven']} of {res['control']['spots']} random spots passed"]
    for r in res["rows"]:
        b, t = r["blind"], r["in_sample"]
        lines.append(f"  {r['variant']:58s} tune {t['bets']:4d} {(t['flat_roi'] or 0):+.1%}  "
                     f"blind {b['bets']:4d} {(b['flat_roi'] or 0):+.1%} {r['verdict']}")
    w = res.get("watch")
    if w:
        lines.append(f"Watch: {w['variant']} blind z {w['blind_z'] or 0:.2f}"
                     + (" -> clears the luck guard, candidate money spot (needs a human look)" if w["clears_luck_guard"]
                        else " (under the luck guard, no units)"))
    return "\n".join(lines)
