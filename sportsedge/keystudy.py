"""NFL key numbers (owner, Oct 31: "Yes test the key numbers for nfl").

NFL games land on 3 and 7 (and 10) far more than on other margins, so half a point around them is worth
more than anywhere else. Does the closing spread misprice that? Every NFL game 2018 on, at the closing
spread and its price, by where the line sits:
- the dog getting the hook past a key number (+3.5, +7.5, +10.5) and the favorite laying less than one
  (-2.5, -6.5, -9.5);
- the line right on the key number (+/-3, +/-7, +/-10: a push there gives the stake back);
- the same, home and road.
Seasons through 2021 tune, 2022 on are blind, with a luck control of random spread bets (same bars as
every study). Only a variant that passes gets money (proven.py).
"""

from __future__ import annotations

import random
from pathlib import Path

from .linestudies import TUNE_LAST, load, verdict
from .nflhist import Record

SPREAD_PRICE = -110.0
KEYS = (3, 7, 10)


def variants(home_spread: float) -> list[tuple[str, str]]:
    """(side, variant) for each key-number spot this closing spread puts a side in."""
    fav = "home" if home_spread < 0 else "away"
    dog = "away" if fav == "home" else "home"
    pts = abs(home_spread)
    out = []
    for k in KEYS:
        if pts == k + 0.5:
            out += [(dog, f"dog +{k + 0.5:g} (hook past {k})"), (fav, f"favorite -{k + 0.5:g} (laying the hook past {k})")]
        if pts == k - 0.5:
            out += [(fav, f"favorite -{k - 0.5:g} (under {k})"), (dog, f"dog +{k - 0.5:g} (short of {k})")]
        if pts == k:
            out += [(fav, f"favorite -{k} (on the number)"), (dog, f"dog +{k} (on the number)")]
    return out + [(s, f"{v} · {'home' if s == 'home' else 'road'}") for s, v in out]


def _result(g, side: str) -> tuple[float, float]:
    m = (g.margin + g.spread) if side == "home" else -(g.margin + g.spread)
    return g.spread_odds.get(side) or SPREAD_PRICE, 1.0 if m > 0 else 0.0 if m < 0 else 0.5


def study(games, n_random: int = 200, seed: int = 31) -> dict:
    rng = random.Random(seed)
    recs: dict = {}
    control: dict = {}

    def put(book, key, g, price, res):
        arm = "blind" if g.season > TUNE_LAST else "in_sample"
        book.setdefault(key, {"in_sample": Record(), "blind": Record()})[arm].add(price, res)

    for g in games:
        if g.season < 2018 or g.spread is None or g.stype == "3":
            continue
        for side, v in variants(g.spread):
            put(recs, v, g, *_result(g, side))
        for k in range(n_random):
            if rng.random() < 0.1:
                put(control, k, g, *_result(g, rng.choice(("home", "away"))))
    rows = [{"study": "NFL key numbers", "sport": "nfl", "market": "spread", "variant": k, "verdict": verdict(r, k),
             **{a: r[a].to_dict() for a in r}} for k, r in sorted(recs.items())]
    fakes = [verdict(r) for r in control.values()]
    return {"rows": rows, "control": {"spots": len(fakes), "proven": sum(1 for x in fakes if x)}}


def run(root="data") -> dict:
    return study(load(Path(root) / "lines" / "espn" / "nfl", "nfl"))


def report(res: dict) -> str:
    c = res["control"]
    lines = [f"NFL key numbers: luck control {c['proven']}/{c['spots']} random spread sets passed."]
    for r in res["rows"]:
        t, b = r["in_sample"], r["blind"]
        f = lambda x: "  -  " if x["flat_roi"] is None else f"{x['flat_roi']:+.1%}"  # noqa: E731
        lines.append(f"  {r['variant']:<48} tune {t['bets']:>4} {f(t)}  blind {b['bets']:>4} {f(b)} {r['verdict']}")
    return "\n".join(lines)


__all__ = ["report", "run", "variants"]
