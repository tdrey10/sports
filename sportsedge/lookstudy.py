"""Lookahead and sandwich spots: is a team flat before (or after) a big game? (study of 2026-11-14)

Idea: a team whose NEXT game this season is against a strong opponent may look past a weaker one
today (lookahead), and one sandwiched between two strong opponents may be flat in the soft middle
game. The market might not price that, so the other side (often a dog) wins more than its price says.
Strength = the opponent's season win % from games played BEFORE today (known when the bet is made;
the schedule itself is public in advance). Strong = top quarter of the tuning seasons' values per
sport (cutoffs never see a blind season), weak = bottom half; each team needs MIN_GAMES earlier games.

Spots, per bet side:
  own lookahead       - this side's next opponent is strong and today's opponent is weak
  opp lookahead       - the OTHER side has that lookahead (bet against the team looking ahead)
  own sandwich        - this side's previous and next opponents are both strong, today's is weak
  opp sandwich        - the other side is sandwiched
  own lookback        - this side's previous opponent was strong, today's is weak (letdown)
  opp lookback        - the other side is in that letdown spot
Tested on +100..+220 moneyline dogs, -170..-120 moneyline favorites and spread (run/puck line) bets at
the real closing price. Closing lines 2018+: seasons through TUNE_LAST tune, later ones are blind.
Luck control: random tenths of all bets through the same bar.
"""

from __future__ import annotations

import random
from pathlib import Path

from .atsstudy import cover_margin
from .linestudies import SMALL_DOG, TUNE_LAST, G, _dog, load, verdict
from .nflhist import Record

STUDY = "42 lookahead and sandwich"
SPORTS = ("nfl", "ncaaf", "nba", "ncaab", "mlb", "nhl")
MIN_GAMES = {"nfl": 3, "ncaaf": 3, "nba": 8, "nhl": 8, "ncaab": 6, "mlb": 15}


def strength(games: list[G], sport: str) -> list[dict]:
    """Each game -> {side: that side's season win % from earlier games, or None with too few}."""
    need = MIN_GAMES[sport]
    rec: dict[str, list[float]] = {}
    season = None
    out = []
    for g in games:
        if g.season != season:
            season = g.season
            rec.clear()
        row = {}
        for side, team in (("home", g.home), ("away", g.away)):
            xs = rec.get(team, [])
            row[side] = sum(xs) / len(xs) if len(xs) >= need else None
        out.append(row)
        for side, team in (("home", g.home), ("away", g.away)):
            rec.setdefault(team, []).append(g.won(side))
    return out


def neighbours(games: list[G]) -> list[dict]:
    """Each game -> {side: (previous game index, next game index) for that team this season}."""
    by_team: dict[tuple[int, str], list[tuple[int, str]]] = {}
    for i, g in enumerate(games):
        for side, team in (("home", g.home), ("away", g.away)):
            by_team.setdefault((g.season, team), []).append((i, side))
    out = [dict() for _ in games]
    for seq in by_team.values():
        for k, (i, side) in enumerate(seq):
            prev = seq[k - 1] if k > 0 else None
            nxt = seq[k + 1] if k + 1 < len(seq) else None
            out[i][side] = (prev, nxt)
    return out


def cutoffs(games: list[G], strong: list[dict]) -> tuple[float, float] | None:
    """(weak = median, strong = top quarter) of the tuning seasons' win % values."""
    xs = sorted(v for g, row in zip(games, strong) if g.season <= TUNE_LAST for v in row.values() if v is not None)
    if len(xs) < 40:
        return None
    return xs[len(xs) // 2], xs[(3 * len(xs)) // 4]


def records_before(games: list[G], sport: str) -> list[dict]:
    """Each game index -> {team: win % from that team's games before this game} for every team seen."""
    need = MIN_GAMES[sport]
    rec: dict[str, list[float]] = {}
    season = None
    snaps = []
    for g in games:
        if g.season != season:
            season = g.season
            rec.clear()
        snaps.append({t: sum(x) / len(x) for t, x in rec.items() if len(x) >= need})
        for side, team in (("home", g.home), ("away", g.away)):
            rec.setdefault(team, []).append(g.won(side))
    return snaps


def side_spots(games: list[G], i: int, side: str, nb: list[dict], snaps: list[dict],
               cut: tuple[float, float]) -> dict[str, bool]:
    """For one team in game i: lookahead / sandwich / lookback flags, using only records before game i."""
    weak, strong_cut = cut
    g = games[i]
    other = "away" if side == "home" else "home"
    today_opp = snaps[i].get(g.away if side == "home" else g.home)
    prev, nxt = nb[i].get(side, (None, None))

    def opp_of(ref):
        if ref is None:
            return None
        j, s = ref
        h = games[j]
        return snaps[i].get(h.away if s == "home" else h.home)  # its record as of today

    p, n = opp_of(prev), opp_of(nxt)
    soft = today_opp is not None and today_opp <= weak
    return {
        "lookahead": soft and n is not None and n >= strong_cut,
        "sandwich": soft and n is not None and n >= strong_cut and p is not None and p >= strong_cut,
        "lookback": soft and p is not None and p >= strong_cut,
        "_other": other,
    }


def spots(games, i, side, nb, snaps, cut) -> list[str]:
    own = side_spots(games, i, side, nb, snaps, cut)
    opp = side_spots(games, i, own["_other"], nb, snaps, cut)
    out = []
    for k in ("lookahead", "sandwich", "lookback"):
        if own[k]:
            out.append(f"own {k}")
        if opp[k]:
            out.append(f"opp {k}")
    return out


def study(games: list[G], sport: str, recs: dict, control: dict, n_random: int = 100, seed: int = 42) -> None:
    games = sorted(games, key=lambda g: g.start)
    snaps = records_before(games, sport)
    strong = strength(games, sport)
    cut = cutoffs(games, strong)
    if cut is None:
        return
    nb = neighbours(games)
    rng = random.Random(f"{seed}:{sport}")
    chosen = [[rng.random() < 0.1 for _ in games] for _ in range(n_random)]

    def put(book, key, g, price, res):
        arm = "blind" if g.season > TUNE_LAST else "in_sample"
        book.setdefault(key, {"in_sample": Record(), "blind": Record()})[arm].add(price, res)

    for i, g in enumerate(games):
        if g.neutral:
            continue
        for side in ("home", "away"):
            kinds = []
            p = g.ml.get(side)
            if _dog(p, *SMALL_DOG):
                kinds.append(("dog ML", p, g.won(side)))
            elif p is not None and -170 <= p <= -120:
                kinds.append(("lock-price fav ML", p, g.won(side)))
            sp = g.spread_odds.get(side) if g.spread is not None else None
            if sp:
                m = cover_margin(g, side)
                kinds.append(("spread", sp, 1.0 if m > 0 else 0.0 if m < 0 else 0.5))
            if not kinds:
                continue
            for k in range(n_random):
                if chosen[k][i]:
                    for _, price, res in kinds:
                        put(control, f"random {k}", g, price, res)
            for s in spots(games, i, side, nb, snaps, cut):
                for kind, price, res in kinds:
                    put(recs, (sport, f"{kind}: {s}"), g, price, res)


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
