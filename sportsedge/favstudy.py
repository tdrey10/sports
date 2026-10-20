"""Lock-type favorites (-170 to -120 moneyline) in spots, tested blind (study of 2026-10-20).

Idea: the daily Lock is always a moneyline favorite from -170 to -120. Which favorites beat that
price: road favorites, favorites off a loss or a blowout win (letdown), rested favorites against a
tired team, favorites on win or losing streaks, favorites facing a team off a blowout loss?
Closing lines 2018+: seasons through TUNE_LAST tune, later ones are blind.
Luck control: random tenths of all -170..-120 favorites through the same bar.
Data: data/lines/espn/<sport>/<season>.csv.gz.
"""

from __future__ import annotations

import random
from pathlib import Path

from .linestudies import BLOWOUT, DAILY, SPORTS, TUNE_LAST, G, annotate, load, verdict
from .nflhist import Record

STUDY = "19 lock favorites"
LOCK = (-170, -120)


def is_lock_price(price) -> bool:
    return price is not None and LOCK[0] <= price <= LOCK[1]


def spots(g: G, side: str, sport: str) -> list[str]:
    p = g.ml.get(side)
    if not is_lock_price(p):
        return []
    other = "away" if side == "home" else "home"
    c, o = g.ctx[side], g.ctx[other]
    out = ["any lock-price favorite"]
    if not g.neutral:
        out.append("home favorite" if side == "home" else "road favorite")
    lm, olm = c["last_margin"], o["last_margin"]
    if lm is not None and lm < 0:
        out.append("favorite off a loss")
    if lm is not None and lm >= BLOWOUT[sport]:
        out.append("favorite off a blowout win")
    if olm is not None and olm <= -BLOWOUT[sport]:
        out.append("favorite vs a team off a blowout loss")
    if c["streak"] >= 3:
        out.append("favorite on a 3+ win streak")
    if c["streak"] <= -2:
        out.append("favorite on a 2+ losing streak")
    if o["streak"] <= -3:
        out.append("favorite vs a team on a 3+ losing streak")
    if sport in DAILY:
        r, ro = g.rest[side], g.rest[other]
        if r is not None and ro is not None:
            if r >= 2 and ro == 1:
                out.append("rested favorite vs a back-to-back")
            if r == 1 and ro >= 2:
                out.append("favorite on a back-to-back vs a rested team")
        if o["in4"] >= 2 and c["in4"] <= 1:
            out.append("favorite vs a team on its 3rd game in 4 days")
    if sport in ("mlb", "nhl") and o["backup"]:
        out.append("favorite vs a team not starting its usual starter")
    return out


def study(games: list[G], sport: str, recs: dict, control: dict, n_random: int = 100, seed: int = 20) -> None:
    rng = random.Random(f"{seed}:{sport}")
    chosen = [[rng.random() < 0.1 for _ in games] for _ in range(n_random)]

    def put(book, key, g, side):
        arm = "blind" if g.season > TUNE_LAST else "in_sample"
        book.setdefault(key, {"in_sample": Record(), "blind": Record()})[arm].add(g.ml[side], g.won(side))

    for i, g in enumerate(games):
        for side in ("home", "away"):
            if not is_lock_price(g.ml.get(side)):
                continue
            for k in range(n_random):
                if chosen[k][i]:
                    put(control, f"random {k}", g, side)
            for s in spots(g, side, sport):
                put(recs, (sport, s), g, side)


def run(lines_dir: Path, sports=SPORTS, random_n: int = 100) -> dict:
    recs: dict = {}
    control: dict = {}
    for sport in sports:
        folder = Path(lines_dir) / sport
        if not any(folder.glob("*.csv.gz")):
            continue
        games = load(folder, sport)
        annotate(games)
        study(games, sport, recs, control, random_n)
    rows = [{"study": STUDY, "sport": k[0], "variant": k[1], "verdict": verdict(r, k[1]),
             **{a: r[a].to_dict() for a in r}} for k, r in sorted(recs.items())]
    fakes = [verdict(r) for r in control.values()]
    return {"rows": rows, "control": {"spots": len(fakes), "proven": sum(1 for x in fakes if x),
                                      "strong": fakes.count("STRONG")}}
