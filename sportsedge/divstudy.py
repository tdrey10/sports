"""Divisional games, tested blind (study of 2026-10-25).

Idea: rivals who play twice a year know each other, so dogs may cover or win more often and
games may stay under. NFL only (the 2006+ archive carries div_game; ESPN's does not).
Bets: moneyline dogs (+100 to +220) and lock-price favorites (-170 to -120), spread sides, and
totals, each in divisional games and, as a mirror, in non-divisional ones. Seasons through
TUNE_LAST tune, later ones are blind. Luck control: random tenths of the same bets through the
same bar. Data: data/lines/nfl/<season>.csv.
"""

from __future__ import annotations

import csv
import random
from pathlib import Path

from .linestudies import SMALL_DOG, TUNE_LAST, _dog, _f, _price, verdict
from .nflhist import Record

STUDY = "24 divisional games"


def is_lock_price(price) -> bool:
    return price is not None and -170 <= price <= -120


def load(folder: Path) -> list[dict]:
    out = []
    for f in sorted(Path(folder).glob("*.csv")):
        with open(f, newline="") as fh:
            for r in csv.DictReader(fh):
                hs, as_ = _f(r["home_score"]), _f(r["away_score"])
                if hs is None or as_ is None or r.get("game_type") != "REG":
                    continue
                out.append({
                    "gid": r["game_id"], "season": int(r["season"]), "div": r.get("div_game") == "1",
                    "hs": hs, "as": as_, "line": _f(r["spread_line"]),  # nflverse: home line, + = home favored
                    "ml": {"home": _price(r["home_moneyline"]), "away": _price(r["away_moneyline"])},
                    "sp": {"home": _price(r["home_spread_odds"]) or -110.0, "away": _price(r["away_spread_odds"]) or -110.0},
                    "total": _f(r["total_line"]),
                    "tot": {"over": _price(r["over_odds"]) or -110.0, "under": _price(r["under_odds"]) or -110.0},
                })
    return out


def bets(g: dict) -> list[tuple[str, float, float]]:
    """(label, price, result) for every bet this game offers; result 1 win, 0 loss, 0.5 push."""
    where = "divisional" if g["div"] else "non-divisional"
    out = []
    for side in ("home", "away"):
        p = g["ml"][side]
        m = g["hs"] - g["as"] if side == "home" else g["as"] - g["hs"]
        kind = "dog" if _dog(p, *SMALL_DOG) else "lock favorite" if is_lock_price(p) else ""
        if kind:
            out.append((f"{where} {kind} moneyline", p, 1.0 if m > 0 else 0.0 if m < 0 else 0.5))
        if g["line"] is not None:
            line = g["line"] if side == "home" else -g["line"]  # + = this side favored
            cover = m - line
            res = 1.0 if cover > 0 else 0.0 if cover < 0 else 0.5
            out.append((f"{where} {'favorite' if line > 0 else 'dog'} spread", g["sp"][side], res))
            if line > 0 and line <= 3:
                out.append((f"{where} short favorite (3 or less) spread", g["sp"][side], res))
            if line < -7:
                out.append((f"{where} big dog (7+) spread", g["sp"][side], res))
    if g["total"] is not None:
        pts = g["hs"] + g["as"]
        for s, res in (("over", pts > g["total"]), ("under", pts < g["total"])):
            out.append((f"{where} {s}", g["tot"][s], 1.0 if res else 0.5 if pts == g["total"] else 0.0))
    return out


def run(folder: Path, random_n: int = 100, seed: int = 25) -> dict:
    games = load(folder)
    rng = random.Random(seed)
    recs: dict = {}
    control: dict = {}

    def put(book, key, g, price, res):
        arm = "blind" if g["season"] > TUNE_LAST else "in_sample"
        book.setdefault(key, {"in_sample": Record(), "blind": Record()})[arm].add(price, res)

    for g in games:
        for label, price, res in bets(g):
            put(recs, label, g, price, res)
            for k in range(random_n):
                if rng.random() < 0.1:
                    put(control, k, g, price, res)
    rows = [{"study": STUDY, "sport": "nfl", "variant": k, "verdict": verdict(r, k),
             **{a: r[a].to_dict() for a in r}} for k, r in sorted(recs.items())]
    fakes = [verdict(r) for r in control.values()]
    return {"rows": rows, "control": {"spots": len(fakes), "proven": sum(1 for x in fakes if x),
                                      "strong": fakes.count("STRONG")}}
