"""Postseason and neutral-site games: are the big-stage prices soft? (2026-11-20)

Idea: playoff games, bowls and March Madness draw the most casual money of the year, and a
neutral court strips out the home edge the books are used to pricing. If the public pushes these
prices around, one side (dogs, favorites, overs, unders) should beat the close in these games.
Also the classic playoff "zig-zag": back the side that lost the previous game of the same
postseason series (NBA/NHL/MLB), or fade it.

Spots (all known before the game, nothing from the game itself leaks in):
  * postseason: ESPN season type 3 (NFL from nflverse: game_type other than REG)
  * postseason, lost / won the last game of this series (same two teams, same postseason, NBA/NHL/MLB)
  * neutral site, regular season (college hoops, college football; NFL London/Germany games)

Bets, each side at the real closing price (slatestudy.bets): +100..+220 dog moneyline,
-170..-120 favorite moneyline, spread dog and spread favorite (football and hoops), over, under.
ESPN closing lines 2018+: seasons through linestudies.TUNE_LAST tune, later ones are blind.
NFL uses the nflverse archive (2006+): tune through nflhist.TUNE_LAST, blind after.
Luck control: random tenths of all bets per sport through the same bar.
"""

from __future__ import annotations

import csv
import random
from datetime import datetime, timezone
from pathlib import Path

from . import nflhist
from .linestudies import TUNE_LAST, G, _f, _price, load, verdict
from .nflhist import Record
from .slatestudy import bets

STUDY = "47 postseason and neutral-site games"
SPORTS = ("nfl", "ncaaf", "nba", "ncaab", "mlb", "nhl")
SERIES_SPORTS = ("nba", "nhl", "mlb")


def tune_last(sport: str) -> int:
    return nflhist.TUNE_LAST if sport == "nfl" else TUNE_LAST


def load_nfl(folder: Path) -> list[G]:
    """nflverse games (2006+) as study games; postseason = any game_type other than REG."""
    out = []
    for f in sorted(Path(folder).glob("*.csv")):
        with open(f, newline="") as fh:
            for r in csv.DictReader(fh):
                hs, as_ = _f(r.get("home_score")), _f(r.get("away_score"))
                if hs is None or as_ is None or not r.get("gameday"):
                    continue
                pair = lambda a, b: {"home": _price(r.get(a)), "away": _price(r.get(b))}  # noqa: E731
                line = _f(r.get("spread_line"))
                out.append(G(
                    "nfl", r["game_id"], int(r["season"]),
                    datetime.fromisoformat(r["gameday"]).replace(tzinfo=timezone.utc),
                    r["home_team"], r["away_team"], hs, as_, r.get("location") == "Neutral",
                    pair("home_moneyline", "away_moneyline"), {"home": None, "away": None},
                    None if line is None else -line, pair("home_spread_odds", "away_spread_odds"),
                    _f(r.get("total_line")), {"over": _price(r.get("over_odds")), "under": _price(r.get("under_odds"))},
                    {"home": _f(r.get("home_rest")), "away": _f(r.get("away_rest"))},
                    None, None, None, None, None, r.get("roof") in ("dome", "closed"), {"home": "", "away": ""},
                    "2" if r.get("game_type") == "REG" else "3",
                    {"home": None, "away": None}, None, {"home": None, "away": None}, {"home": None, "away": None},
                ))
    return sorted(out, key=lambda g: (g.start, g.gid))


def series_last(games: list[G]) -> list[dict]:
    """For each postseason game: side -> 'won'/'lost' in the previous game of the same series
    (same two teams, same season, both postseason). Empty when it is the first meeting."""
    last: dict = {}
    out = []
    for g in games:
        info: dict = {}
        if g.stype == "3":
            key = (g.season, frozenset((g.home, g.away)))
            prev = last.get(key)
            if prev is not None:
                for side, team in (("home", g.home), ("away", g.away)):
                    info[side] = "won" if prev == team else "lost" if prev else None
            m = g.hs - g.as_
            last[key] = g.home if m > 0 else g.away if m < 0 else ""
        out.append(info)
    return out


def side_of(kind: str, g: G) -> str | None:
    """Which side a bet kind is on (totals have none)."""
    for side in ("home", "away"):
        p = g.ml.get(side)
        if kind == "dog ML" and p is not None and 100 <= p <= 220:
            return side
        if kind == "lock-price fav ML" and p is not None and -170 <= p <= -120:
            return side
        if kind.startswith("spread") and g.spread:
            line = g.spread if side == "home" else -g.spread
            if (kind == "spread dog") == (line > 0):
                return side
    return None


def spots(g: G, series: dict) -> list[tuple[str, str | None]]:
    """(spot label, side it applies to or None for both sides/totals)."""
    out: list[tuple[str, str | None]] = []
    if g.stype == "3":
        out.append(("postseason", None))
        if g.sport in SERIES_SPORTS:
            for side in ("home", "away"):
                if series.get(side) == "lost":
                    out.append(("postseason, lost the last series game", side))
                elif series.get(side) == "won":
                    out.append(("postseason, won the last series game", side))
    elif g.neutral and g.sport in ("ncaab", "ncaaf", "nfl"):
        out.append(("neutral site, regular season", None))
    return out


def study(games: list[G], sport: str, recs: dict, control: dict, n_random: int = 100, seed: int = 47) -> None:
    cut = tune_last(sport)
    rng = random.Random(f"{seed}:{sport}")
    chosen = [[rng.random() < 0.1 for _ in games] for _ in range(n_random)]
    series = series_last(games)

    def put(book, key, g, price, res):
        if res is None:
            return
        arm = "blind" if g.season > cut else "in_sample"
        book.setdefault(key, {"in_sample": Record(), "blind": Record()})[arm].add(price, res)

    for i, g in enumerate(games):
        kinds = bets(g)
        if not kinds:
            continue
        for k in range(n_random):
            if chosen[k][i]:
                for _, price, res in kinds:
                    put(control, (sport, k), g, price, res)
        for label, only in spots(g, series[i]):
            for kind, price, res in kinds:
                if only is not None:
                    if kind in ("over", "under") or side_of(kind, g) != only:
                        continue
                put(recs, (sport, f"{kind}: {label}"), g, price, res)


def run(lines_dir: Path, nfl_dir: Path | None = None, sports=SPORTS, random_n: int = 100) -> dict:
    recs: dict = {}
    control: dict = {}
    for sport in sports:
        if sport == "nfl":
            if nfl_dir is None or not any(Path(nfl_dir).glob("*.csv")):
                continue
            games = load_nfl(nfl_dir)
        else:
            folder = Path(lines_dir) / sport
            if not any(folder.glob("*.csv.gz")):
                continue
            games = load(folder, sport)
        study(games, sport, recs, control, random_n)
    rows = [{"study": STUDY, "sport": k[0], "variant": k[1], "verdict": verdict(r, k[1]),
             **{a: r[a].to_dict() for a in r}} for k, r in sorted(recs.items())]
    fakes = [verdict(r) for r in control.values()]
    return {"rows": rows, "control": {"spots": len(fakes), "proven": sum(1 for x in fakes if x),
                                      "strong": fakes.count("STRONG")}}
