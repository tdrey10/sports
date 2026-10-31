"""How to bet the proven spots better (owner, Oct 31: "you need to do some studies and be better, more
efficient").

Three questions about the money spots we already have (the game-factor spots that passed, 2018 on):
1. Timing: does the opening price beat the closing price? (ESPN keeps opening moneylines from 2023.) If
   the spot's price usually moves our way, the bet should go up as early as possible.
2. Stacking: when two or more proven spots back the same team in the same game, does it win more?
3. Price range: within each spot, which odds range made the money (a spot can pay on short favorites and
   lose on long ones)?
4. Our ratings vs the market (owner, Oct 31, after Boston College lost 27-2 with our ratings at Duke by 16:
   "do a shit load more studies"): split each spot's bets by how far our own power ratings' projected margin
   for the team sits from the market's (the closing spread, or the moneyline's no-vig chance turned into
   points). Do the spots lose when our ratings think the team is much worse than the price says?
   Tuned 2018-2021, checked blind 2022 on.
5. Line movement (Oct 31): do the spots win more when the price has moved toward our team since it opened
   (the market agrees) than when it moved away? Opening prices exist from 2023, so it is all blind years.
Every number is a flat 1-unit bet. Sample sizes are printed: a split with few bets is a hint, not proof.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from .factorstudy import FOOTBALL, GAME_WIDE, Tracker, _markets, active
from .linestudies import TUNE_LAST, _fair, load
from .nflhist import Record

SCALE = {"nfl": 7.0, "ncaaf": 7.0, "nba": 5.0, "ncaab": 5.0, "nhl": 0.75, "mlb": 1.0}  # "much" = one score
SD = {"nfl": 13.5, "ncaaf": 16.0, "nba": 12.0, "ncaab": 11.2, "nhl": 2.3, "mlb": 4.3}  # margin spread, for ML -> points

BANDS = {"moneyline favorite": [(-150, -131), (-130, -116), (-115, -101)],
         "moneyline dog": [(100, 149), (150, 199), (200, 300)]}


def _passed(root) -> dict[str, dict]:
    out = defaultdict(dict)
    for sport, res in json.loads((Path(root) / "factor_study.json").read_text()).items():
        for r in res.get("rows", []):
            if r.get("verdict") and "its own history" not in r["variant"] and r["market"].startswith("moneyline"):
                out[sport][(r["market"], r["variant"])] = r
    return out


def _band(market: str, price: float) -> str | None:
    for lo, hi in BANDS.get(market, []):
        if lo <= price <= hi:
            return f"{lo:+d} to {hi:+d}"
    return None


def run_sport(lines_dir: Path, sport: str, spots: dict) -> dict:
    games = load(Path(lines_dir) / sport, sport)
    tr = Tracker(sport)
    venue = sport in FOOTBALL + ("mlb",)
    timing = defaultdict(lambda: {"open": Record(), "close": Record(), "moved_our_way": 0, "n": 0})
    from statistics import NormalDist

    from .effstudy import Ratings, fit_k

    reg = [x for x in games if x.stype != "3"]
    rt = Ratings(sport, fit_k(reg, sport)) if sport in SCALE else None
    agree = defaultdict(lambda: {"in_sample": Record(), "blind": Record()})
    half = defaultdict(lambda: {"in_sample": Record(), "blind": Record()})
    nd = NormalDist()
    stack = defaultdict(Record)
    move = defaultdict(lambda: {"in_sample": Record(), "blind": Record()})
    bands = defaultdict(Record)
    for g in games:
        tr._new_season(g.season)
        if rt is not None and g.stype != "3":
            rt.new_season(g.season)
        proj = None
        if rt is not None and g.stype != "3" and rt.ready(g.home, g.away):
            ph, pa = rt.project(g.home, g.away, g.neutral)
            proj = ph - pa  # home margin
        if g.season >= 2018 and g.stype != "3":
            kw = {"tzo": g.tzo, "elev": g.elev, "indoor": g.indoor if venue else True, "temp": g.temp, "wind": g.wind,
                  "rain": g.rain}
            c = {"home": tr.ctx(g.home, g.away, "home", g.start, g.neutral, **kw),
                 "away": tr.ctx(g.away, g.home, "away", g.start, g.neutral, **kw)}
            for side in ("home", "away"):
                other = "away" if side == "home" else "home"
                acts = [f"{d}: {n}" for d, n in active(sport, c[side], c[other]) if n not in GAME_WIDE]
                for market, price, res in _markets(g, side):
                    hits = [v for v in acts if (market, v) in spots]
                    if not hits:
                        continue
                    stack["2+ spots" if len(hits) > 1 else "1 spot"].add(price, res)
                    if proj is not None:
                        if g.spread is not None:
                            mkt = -g.spread
                        else:
                            fair = _fair(g.ml)
                            ph_ = fair.get("home") if fair else None
                            mkt = nd.inv_cdf(min(max(ph_, 0.02), 0.98)) * SD[sport] if ph_ else None
                        if mkt is not None:
                            gap = (proj - mkt) if side == "home" else (mkt - proj)  # + : our ratings like this team more
                            s_ = SCALE[sport]
                            bucket = ("ratings much worse than the price" if gap <= -s_ else
                                      "ratings much better than the price" if gap >= s_ else "ratings close to the price")
                            arm = "blind" if g.season > TUNE_LAST else "in_sample"
                            agree[bucket][arm].add(price, res)
                    if g.h1_spread is not None and g.h1 and g.h1.get("home") is not None and g.h1.get("away") is not None:
                        m1 = g.h1["home"] - g.h1["away"]
                        m = (m1 + g.h1_spread) if side == "home" else -(m1 + g.h1_spread)
                        hp = (g.h1_spread_odds or {}).get(side) or -110.0
                        arm = "blind" if g.season > TUNE_LAST else "in_sample"
                        for v in hits:
                            half[(market, v)][arm].add(hp, 1.0 if m > 0 else 0.0 if m < 0 else 0.5)
                    for v in hits:
                        b = _band(market, price)
                        if b:
                            bands[(market, v, b)].add(price, res)
                        op = (g.open_ml or {}).get(side)
                        if op is not None:
                            t = timing[(market, v)]
                            t["open"].add(op, res)
                            t["close"].add(price, res)
                            t["n"] += 1
                            t["moved_our_way"] += op > price  # the price shortened after the open: we beat the close
                            mv = ("line moved our way" if op > price else "line moved against us" if op < price
                                  else "line didn't move")
                            move[mv]["blind" if g.season > TUNE_LAST else "in_sample"].add(price, res)
        tr.add(g)
        if rt is not None and g.stype != "3":
            rt.add(g)
    rec = lambda r: {"bets": r.bets, "roi": None if r.flat_roi is None else round(r.flat_roi, 4)}  # noqa: E731
    return {
        "timing": [{"market": k[0], "spot": k[1], "bets": t["n"], "open": rec(t["open"]), "close": rec(t["close"]),
                    "moved_our_way": round(t["moved_our_way"] / t["n"], 3) if t["n"] else None}
                   for k, t in sorted(timing.items())],
        "stacking": {k: rec(r) for k, r in sorted(stack.items())},
        "bands": [{"market": k[0], "spot": k[1], "odds": k[2], **rec(r)} for k, r in sorted(bands.items())],
        "first_half": [{"market": k[0], "spot": k[1], "in_sample": rec(v["in_sample"]), "blind": rec(v["blind"])}
                       for k, v in sorted(half.items())],
        "movement": {k: {"in_sample": rec(v["in_sample"]), "blind": rec(v["blind"])} for k, v in sorted(move.items())},
        "ratings": {k: {"in_sample": rec(v["in_sample"]), "blind": rec(v["blind"])} for k, v in sorted(agree.items())},
    }


def run_all(root="data") -> dict:
    root = Path(root)
    out = {}
    for sport, spots in _passed(root).items():
        out[sport] = run_sport(root / "lines" / "espn", sport, spots)
    return out


def report(res: dict) -> str:
    pct = lambda r: "  -  " if r["roi"] is None else f"{r['roi']:+.1%}"  # noqa: E731
    lines = []
    for sport, r in res.items():
        lines.append(f"{sport.upper()}")
        for t in r["timing"]:
            lines.append(f"  timing  {t['spot']:<45} {t['bets']:>4} bets  open {pct(t['open'])}  close {pct(t['close'])}  "
                         f"moved our way {t['moved_our_way']:.0%}" if t["moved_our_way"] is not None else "")
        for k, v in r["stacking"].items():
            lines.append(f"  stack   {k:<45} {v['bets']:>4} bets  {pct(v)}")
        for h in r.get("first_half", []):
            lines.append(f"  1st half spread {h['spot']:<37} tune {h['in_sample']['bets']:>4} {pct(h['in_sample'])}  blind {h['blind']['bets']:>4} {pct(h['blind'])}")
        for k, v in r.get("movement", {}).items():
            lines.append(f"  move    {k:<45} tune {v['in_sample']['bets']:>4} {pct(v['in_sample'])}  blind {v['blind']['bets']:>4} {pct(v['blind'])}")
        for k, v in r.get("ratings", {}).items():
            lines.append(f"  ratings {k:<45} tune {v['in_sample']['bets']:>4} {pct(v['in_sample'])}  blind {v['blind']['bets']:>4} {pct(v['blind'])}")
        for b in r["bands"]:
            lines.append(f"  odds    {b['spot'][:34]:<34} {b['odds']:<11} {b['bets']:>4} bets  {pct(b)}")
    return "\n".join(lines)


__all__ = ["report", "run_all"]
