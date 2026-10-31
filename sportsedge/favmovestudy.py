"""Lock-price favorites by how the line moved from the opener, tested blind (study of 2026-10-31).

Idea: dogs whose price moved were tested on 2026-10-19; here the favorite side. Bet the moneyline
favorite at the closing price (-170 to -120) when its no-vig win chance ROSE (steam toward the
favorite) or FELL (the market backed off) by 1/2/3+ points since the opener, and the mirror dog
(+100 to +220) on the other side of each move. Opening lines start 2023: seasons through
OPEN_TUNE_LAST tune, later ones are blind.
Luck control: random tenths of all games, favorite or dog side, through the same bar.
Data: data/lines/espn/<sport>/<season>.csv.gz.
"""

from __future__ import annotations

import random
from pathlib import Path

from .linestudies import OPEN_TUNE_LAST, SPORTS, G, _fair, load, verdict
from .nflhist import Record

STUDY = "27 favorite line moves from the opener"
STEPS = (0.01, 0.02, 0.03)
LOCK = (-170, -120)
DOG = (100, 220)


def move(g: G) -> float | None:
    """Home no-vig win chance at the close minus at the open (None without both prices)."""
    o, c = _fair(g.open_ml), _fair(g.ml)
    return None if o is None or c is None else c["home"] - o["home"]


def spots(g: G) -> list[tuple[str, str]]:
    """(label, side) bets this game's move makes at closing prices."""
    m = move(g)
    if m is None:
        return []
    out = []
    for side in ("home", "away"):
        d = m if side == "home" else -m  # that side's win chance change
        p = g.ml.get(side)
        if p is None:
            continue
        kind = "lock fav" if LOCK[0] <= p <= LOCK[1] else "dog" if DOG[0] <= p <= DOG[1] else None
        if kind is None:
            continue
        for s in STEPS:
            if d >= s:
                out.append((f"{kind} win chance rose {int(s * 100)}+ pts since open", side))
            if d <= -s:
                out.append((f"{kind} win chance fell {int(s * 100)}+ pts since open", side))
    return out


def study(games: list[G], sport: str, recs: dict, control: dict, n_random: int = 100, seed: int = 31) -> None:
    rng = random.Random(f"{seed}:{sport}")
    cut = OPEN_TUNE_LAST  # MLB's 2019-21 openers equal the close (no move), so 2023 is its first tuning year

    def put(book, key, g, price, res):
        arm = "blind" if g.season > cut else "in_sample"
        book.setdefault(key, {"in_sample": Record(), "blind": Record()})[arm].add(price, res)

    for g in games:
        picks = [rng.random() < 0.1 for _ in range(n_random)]
        if g.margin == 0 or move(g) is None:
            continue
        for k, hit in enumerate(picks):
            if hit:
                side = "home" if rng.random() < 0.5 else "away"
                p = g.ml.get(side)
                if p is not None and (LOCK[0] <= p <= LOCK[1] or DOG[0] <= p <= DOG[1]):
                    put(control, f"random {k}", g, p, g.won(side))
        for label, side in spots(g):
            put(recs, (sport, label), g, g.ml[side], g.won(side))


def run(lines_dir: Path, sports=SPORTS, random_n: int = 100) -> dict:
    recs: dict = {}
    control: dict = {}
    for sport in sports:
        folder = Path(lines_dir) / sport
        if not any(folder.glob("*.csv.gz")):
            continue
        study(load(folder, sport), sport, recs, control, random_n)
    rows = [{"study": STUDY, "sport": k[0], "variant": k[1], "verdict": verdict(r, k[1]),
             **{a: r[a].to_dict() for a in r}} for k, r in sorted(recs.items())]
    fakes = [verdict(r) for r in control.values()]
    return {"rows": rows, "control": {"spots": len(fakes), "proven": sum(1 for x in fakes if x),
                                      "strong": fakes.count("STRONG")}}
