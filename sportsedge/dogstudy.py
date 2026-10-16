"""Dogs in rematches, low-total games and form flips, tested blind (study of 2026-10-16).

Idea: a +100..+220 closing-price dog may win more than its price says when (a) it lost the last
meeting with this opponent (revenge), (b) it won the last meeting, (c) the game's total sits in
the bottom quarter of the sport's totals (low scoring means more upsets), or (d) its last-4
average margin beats the favorite's even though the market made it the dog (form flip).
Seasons through 2021 tune (the cut for "low total" and the tuning record), 2022 on are blind.
Luck control: random tenths of all games betting a +100..+220 dog through the same bar.
Data: data/lines/espn/<sport>/<season>.csv.gz.
"""

from __future__ import annotations

import random
from collections import defaultdict, deque
from pathlib import Path

from .linestudies import SMALL_DOG, SPORTS, TUNE_LAST, G, _dog, annotate, load, verdict
from .nflhist import Record

STUDY = "17 dog spots"
LOW = 0.25  # bottom quarter of the tuning seasons' totals


def features(games: list[G]) -> list[dict]:
    """Per game, only what was known before kickoff: the last meeting's winner (this and the
    prior season) and each side's average margin over its last 4 games (None until 4 played)."""
    meet: dict[frozenset, str] = {}  # pair -> winner of their last meeting
    form: dict[str, deque] = defaultdict(lambda: deque(maxlen=4))
    season, out = None, []
    for g in games:
        if g.season != season:
            season = g.season
            form.clear()
        pair = frozenset((g.home, g.away))
        last = meet.get(pair)
        f = {"last_winner": last,
             "form": {s: (sum(form[t]) / 4 if len(form[t]) == 4 else None)
                      for s, t in (("home", g.home), ("away", g.away))}}
        out.append(f)
        if g.margin != 0:
            meet[pair] = g.home if g.margin > 0 else g.away
        form[g.home].append(g.margin)
        form[g.away].append(-g.margin)
    return out


def _cut(games: list[G]) -> float | None:
    t = sorted(g.total for g in games if g.total and g.season <= TUNE_LAST)
    return t[int(len(t) * LOW)] if len(t) >= 200 else None


def study(games: list[G], sport: str, recs: dict, control: dict, n_random: int = 100, seed: int = 13) -> None:
    feats = features(games)
    cut = _cut(games)
    if cut is None:
        return
    rng = random.Random(f"{seed}:{sport}")
    chosen = [[rng.random() < 0.1 for _ in games] for _ in range(n_random)]

    def put(book, key, g, side):
        arm = "blind" if g.season > TUNE_LAST else "in_sample"
        book.setdefault(key, {"in_sample": Record(), "blind": Record()})[arm].add(g.ml[side], g.won(side))

    for i, (g, f) in enumerate(zip(games, feats)):
        for side in ("home", "away"):
            if not _dog(g.ml[side], *SMALL_DOG):
                continue
            team = g.home if side == "home" else g.away
            for k in range(n_random):
                if chosen[k][i]:
                    put(control, f"random {k}", g, side)
            fav = "away" if side == "home" else "home"
            spots = []
            if f["last_winner"]:
                spots.append("dog lost the last meeting (revenge)" if f["last_winner"] != team
                             else "dog won the last meeting")
            if g.total and g.total <= cut:
                spots.append("dog in a low-total game (bottom quarter)")
            if f["form"][side] is not None and f["form"][fav] is not None and f["form"][side] > f["form"][fav]:
                spots.append("dog with better last-4 margin than the favorite")
            for s in spots:
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
