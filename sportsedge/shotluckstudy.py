"""Soccer shot luck: do teams whose goals ran ahead of (or behind) their shots on target
regress, and does shot dominance the table hides beat the price? (2026-11-21)

Idea: goals in soccer are rare and noisy; shots on target are steadier. A team that has scored
more (or let in less) than its shots on target say is "running hot" on finishing or keeping, and
the market may price the results, not the shots. Back the unlucky side, fade the lucky one; back
sides that out-shoot opponents while their results look poor; and for totals, use both teams'
shot volume and how far their recent games' goals ran ahead of the shots.

Data: data/games/<league> (results, closing 3-way moneylines, 2.5 totals) joined to
data/boxscores/<league> (ESPN shots, shots on target, possession per side), every match since
Aug 2024 for history; prices from the 2025-26 season. Before each match, from earlier matches
only (last 8, at least 5): per game goals minus k * shots on target, for and against, where k is
the league-wide goals-per-shot-on-target from tuning matches only. Cutoffs (top / bottom 20%)
come from tuning matches only. Matches before soccerstudy.TUNE_END tune; later ones are blind.
Luck control: random spots (a random side on a random slice of priced matches) through the same
bar. The bar is soccerstudy.proven (60+ blind bets, blind ROI minus its SE above 0, tuning ROI
above 0).
"""

from __future__ import annotations

import json
import random
from collections import defaultdict, deque
from pathlib import Path

from . import soccerstudy as S

WINDOW = 8
MIN_GAMES = 5
DOG_LO, DOG_HI = 100, 220


def load(games_dir: Path, box_dir: Path, leagues=S.LEAGUES) -> list[dict]:
    """soccerstudy rows (deduped, sorted) with the match's own box stats attached (used only for
    LATER matches' history, never for the match itself)."""
    import csv
    ids = {}
    for lg in leagues:
        for f in sorted(Path(games_dir, lg).glob("*.csv")):
            with open(f, newline="", encoding="utf-8") as fh:
                for r in csv.DictReader(fh):
                    if r.get("date"):
                        ids[(r["date"][:10], r["home"], r["away"])] = (lg, r["event_id"])
    box = {}
    for lg in leagues:
        for f in sorted(Path(box_dir, lg).glob("*.json")):
            for eid, v in json.loads(f.read_text()).items():
                box[(lg, eid)] = v
    rows = S.load(games_dir, leagues)
    for r in rows:
        key = ids.get((r["date"][:10], r["home"], r["away"]))
        b = box.get(key) if key else None
        r["box"] = b if b and all(b.get(s, {}).get("sot") is not None for s in ("home", "away")) else None
    S.annotate(rows)
    return rows


def goals_per_sot(rows: list[dict]) -> float:
    g = s = 0.0
    for r in rows:
        if r["date"] < S.TUNE_END and r.get("box"):
            g += r["hs"] + r["as"]
            s += r["box"]["home"]["sot"] + r["box"]["away"]["sot"]
    return g / s if s else 0.3


def features(rows: list[dict], k: float) -> None:
    """r['shot'][side] from that side's last WINDOW boxed matches before this one."""
    hist: dict[str, deque] = defaultdict(lambda: deque(maxlen=WINDOW))
    for r in rows:
        out = {}
        for side, team in (("home", r["home"]), ("away", r["away"])):
            h = list(hist[team])
            if len(h) < MIN_GAMES:
                out[side] = None
                continue
            n = len(h)
            gf = sum(x[0] for x in h) / n
            ga = sum(x[1] for x in h) / n
            sf = sum(x[2] for x in h) / n
            sa = sum(x[3] for x in h) / n
            out[side] = {"luck": (gf - k * sf) - (ga - k * sa),
                         "share": sf / (sf + sa) if sf + sa else 0.5,
                         "vol": sf + sa,
                         "goal_luck": (gf + ga) - k * (sf + sa),
                         "poss": sum(x[4] for x in h) / n}
        r["shot"] = out
        b = r.get("box")
        if b:
            for side, opp, team, gf, ga in (("home", "away", r["home"], r["hs"], r["as"]),
                                            ("away", "home", r["away"], r["as"], r["hs"])):
                hist[team].append((gf, ga, b[side]["sot"], b[opp]["sot"], b[side].get("poss_pct") or 50.0))


def cutoffs(rows: list[dict]) -> dict:
    """20th / 80th percentiles of each feature over tuning matches only."""
    vals = defaultdict(list)
    for r in rows:
        if r["date"] >= S.TUNE_END:
            continue
        for side in ("home", "away"):
            f = r["shot"].get(side)
            if f:
                for key, v in f.items():
                    vals[key].append(v)
    out = {}
    for key, xs in vals.items():
        xs.sort()
        out[key] = (xs[int(0.2 * (len(xs) - 1))], xs[int(0.8 * (len(xs) - 1))])
    return out


def _opp(side):
    return "away" if side == "home" else "home"


def _dog(r, side):
    p = r["ml"].get(side)
    return p is not None and DOG_LO <= p <= DOG_HI


def spots(q: dict) -> dict:
    """name -> (side or 'either', test(r, side))."""
    def f(r, side):
        return r["shot"].get(side)

    def both(r):
        return f(r, "home") and f(r, "away")

    side_tests = {
        "unlucky side (luck bottom 20%)": lambda r, s: f(r, s)["luck"] <= q["luck"][0],
        "opponent lucky (luck top 20%)": lambda r, s: f(r, _opp(s))["luck"] >= q["luck"][1],
        "unlucky vs lucky": lambda r, s: f(r, s)["luck"] <= q["luck"][0] and f(r, _opp(s))["luck"] >= q["luck"][1],
        "shot-dominant (SoT share top 20%) with poor form (5 or fewer pts in last 5)":
            lambda r, s: f(r, s)["share"] >= q["share"][1] and r["ctx"][s]["pts5"] <= 5,
        "shot-dominant dog (+100 to +220)": lambda r, s: f(r, s)["share"] >= q["share"][1] and _dog(r, s),
        "unlucky dog (+100 to +220)": lambda r, s: f(r, s)["luck"] <= q["luck"][0] and _dog(r, s),
        "fade outshot side in good form (opp SoT share bottom 20%, 10+ pts in last 5)":
            lambda r, s: f(r, _opp(s))["share"] <= q["share"][0] and r["ctx"][_opp(s)]["pts5"] >= 10,
        "low-possession side vs high-possession side (bottom vs top 20%)":
            lambda r, s: f(r, s)["poss"] <= q["poss"][0] and f(r, _opp(s))["poss"] >= q["poss"][1],
    }
    out = {}
    for name, t in side_tests.items():
        for side in ("home", "away"):
            out[f"{side}: {name}"] = (side, lambda r, s=side, t=t: bool(both(r)) and t(r, s))
    for name in ("unlucky side (luck bottom 20%)", "shot-dominant dog (+100 to +220)", "unlucky dog (+100 to +220)",
                 "shot-dominant (SoT share top 20%) with poor form (5 or fewer pts in last 5)"):
        t = side_tests[name]
        out[f"either side: {name}"] = ("either", lambda r, s, t=t: bool(both(r)) and t(r, s))
    out["over: both high shot volume (top 20%)"] = (
        "over", lambda r, s: bool(both(r)) and f(r, "home")["vol"] >= q["vol"][1] and f(r, "away")["vol"] >= q["vol"][1])
    out["under: both low shot volume (bottom 20%)"] = (
        "under", lambda r, s: bool(both(r)) and f(r, "home")["vol"] <= q["vol"][0] and f(r, "away")["vol"] <= q["vol"][0])
    out["under: both teams' games ran ahead of their shots (goal luck top 20%)"] = (
        "under", lambda r, s: bool(both(r)) and f(r, "home")["goal_luck"] >= q["goal_luck"][1]
        and f(r, "away")["goal_luck"] >= q["goal_luck"][1])
    out["over: both teams' games ran behind their shots (goal luck bottom 20%)"] = (
        "over", lambda r, s: bool(both(r)) and f(r, "home")["goal_luck"] <= q["goal_luck"][0]
        and f(r, "away")["goal_luck"] <= q["goal_luck"][0])
    out["draw: even shot shares (both 45-55%)"] = (
        "draw", lambda r, s: bool(both(r)) and all(0.45 <= f(r, x)["share"] <= 0.55 for x in ("home", "away")))
    return out


def run_spot(rows: list[dict], side: str, test) -> tuple[dict, dict]:
    out = {"tune": [], "blind": []}
    for r in rows:
        sides = ("home", "away") if side == "either" else (side,)
        for s in sides:
            if not test(r, s):
                continue
            x = S._bet(r, s)
            if x is not None:
                out["tune" if r["date"] < S.TUNE_END else "blind"].append(x)
    return S._record(out["tune"]), S._record(out["blind"])


def run(games_dir: Path, box_dir: Path, random_n: int = 300, seed: int = 21) -> dict:
    rows = load(games_dir, box_dir)
    k = goals_per_sot(rows)
    features(rows, k)
    q = cutoffs(rows)
    priced = [r for r in rows if r["ml"]["home"] is not None]
    res = {"matches": len(rows), "boxed": sum(1 for r in rows if r.get("box")), "priced": len(priced),
           "goals_per_sot": round(k, 4), "cutoffs": {a: [round(x, 3) for x in b] for a, b in q.items()}, "rows": []}
    for name, (side, test) in spots(q).items():
        t, b = run_spot(priced, side, test)
        res["rows"].append({"spot": name, "side": side, "tuning": t, "blind": b, "proven": S.proven(t, b)})
    rng = random.Random(seed)
    passed = 0
    for _ in range(random_n):
        side = rng.choice(["home", "draw", "away", "over", "under"])
        frac = rng.uniform(0.05, 0.3)
        sel = [r for r in priced if rng.random() < frac]
        t, b = run_spot(sel, side, lambda r, s: True)
        passed += S.proven(t, b)
    n = len(res["rows"])
    res["control"] = {"random_spots": random_n, "passed": passed,
                      "expected_by_luck": round(passed / random_n * n, 2), "variants": n}
    return res


def report(res: dict) -> str:
    def f(x):
        return f"{x['bets']} bets {x['roi']:+.1%} (±{x['se']:.1%})" if x["roi"] is not None else f"{x['bets']} bets"

    out = [f"Soccer shot luck: {res['matches']} matches, {res['boxed']} with box scores, {res['priced']} priced; "
           f"k = {res['goals_per_sot']} goals per shot on target (tuning)"]
    for r in res["rows"]:
        out.append(f"  {r['spot']}: tuning {f(r['tuning'])} | blind {f(r['blind'])}" + ("  PROVEN" if r["proven"] else ""))
    c = res["control"]
    out.append(f"  luck control: {c['passed']}/{c['random_spots']} random spots pass; luck alone gives "
               f"~{c['expected_by_luck']} passes over {c['variants']} variants")
    return "\n".join(out)


if __name__ == "__main__":  # python -m sportsedge.shotluckstudy
    res = run(Path("data/games"), Path("data/boxscores"))
    print(report(res))
