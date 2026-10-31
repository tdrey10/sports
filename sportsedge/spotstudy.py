"""How to bet the proven spots better (owner, Oct 31: "you need to do some studies and be better, more
efficient").

Three questions about the money spots we already have (the game-factor spots that passed, 2018 on):
1. Timing: does the opening price beat the closing price? (ESPN keeps opening moneylines from 2023.) If
   the spot's price usually moves our way, the bet should go up as early as possible.
2. Stacking: when two or more proven spots back the same team in the same game, does it win more?
3. Price range: within each spot, which odds range made the money (a spot can pay on short favorites and
   lose on long ones)?
Every number is a flat 1-unit bet. Sample sizes are printed: a split with few bets is a hint, not proof.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from .factorstudy import FOOTBALL, GAME_WIDE, Tracker, _markets, active
from .linestudies import load
from .nflhist import Record

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
    stack = defaultdict(Record)
    bands = defaultdict(Record)
    for g in games:
        tr._new_season(g.season)
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
        tr.add(g)
    rec = lambda r: {"bets": r.bets, "roi": None if r.flat_roi is None else round(r.flat_roi, 4)}  # noqa: E731
    return {
        "timing": [{"market": k[0], "spot": k[1], "bets": t["n"], "open": rec(t["open"]), "close": rec(t["close"]),
                    "moved_our_way": round(t["moved_our_way"] / t["n"], 3) if t["n"] else None}
                   for k, t in sorted(timing.items())],
        "stacking": {k: rec(r) for k, r in sorted(stack.items())},
        "bands": [{"market": k[0], "spot": k[1], "odds": k[2], **rec(r)} for k, r in sorted(bands.items())],
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
        for b in r["bands"]:
            lines.append(f"  odds    {b['spot'][:34]:<34} {b['odds']:<11} {b['bets']:>4} bets  {pct(b)}")
    return "\n".join(lines)


__all__ = ["report", "run_all"]
