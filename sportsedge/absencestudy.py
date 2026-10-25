"""Player-absence study (owner, Oct 25: "each team and player needs to be studied, it will help with
picks and value plays").

When a team's most important players sit, does the closing line already price it, or is there
value? From the player box scores (data/players/<sport>/<season>.json) each game knows, before
it starts, every player's share of his team's production so far this season (NBA: game score;
NHL: ice time + points), and the box score says who didn't play. A player counts as an absence
only if he played at least half of the team's games before this one.

Rules, each on the moneyline (dogs +100..+300, favorites to -170) and, outside hockey, against
the spread, at the closing price: back or fade a team missing its top player; missing 2+ of its
top 3; missing 25%+ of its production; and the same from the opponent's side. The first 60% of
the games (by date) tune, the rest are blind; the luck control runs the same bars on random
tenths of all games. Only a STRONG variant is worth money.
"""

from __future__ import annotations

import csv
import json
import random
from collections import defaultdict
from pathlib import Path

from .nflhist import Record
from .linestudies import verdict

TUNE_SHARE = 0.6
MIN_GAMES = 8  # team games played before a team's absences count
DOG = (100, 300)
MAX_FAV = -170
SPREAD_PRICE = -110.0


def _f(v) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x


def _lines(root, sport: str) -> dict[str, dict]:
    """gid -> closing prices and the result, from data/games/<sport>/*.csv."""
    out = {}
    for f in sorted((Path(root) / "games" / sport).glob("*.csv")):
        with f.open() as fh:
            for r in csv.DictReader(fh):
                hs, as_ = _f(r.get("home_score")), _f(r.get("away_score"))
                if hs is None or as_ is None:
                    continue
                out[r["event_id"]] = {"home": r["home"], "away": r["away"], "margin": hs - as_,
                                      "ml": {"home": _f(r.get("close_home_ml")), "away": _f(r.get("close_away_ml"))},
                                      "spread": _f(r.get("close_home_spread")),
                                      "spread_odds": {"home": _f(r.get("close_home_spread_price")),
                                                      "away": _f(r.get("close_away_spread_price"))}}
    return out


def _value(sport: str, row) -> tuple[str, str, float]:
    """(team, player, production) for one box-score row."""
    if sport == "nba":
        team, name = row[0], row[1]
        pts, reb, ast, fgm, fga, ftm, fta = row[5], row[6], row[7], row[9], row[10], row[13], row[14]
        return team, name, max(pts + 0.4 * fgm - 0.7 * fga - 0.4 * (fta - ftm) + 0.5 * reb + 0.7 * ast, 0.0)
    team, name, group, st = row  # nhl
    if group == "goalies":
        return team, name, 0.0
    return team, name, st.get("TOI", 0.0) / 20 + st.get("G", 0.0) + st.get("A", 0.0)


def features(root, sport: str) -> list[dict]:
    """Per game with a closing line and box score, in date order: each side's absences before tip."""
    games = {}
    for f in sorted((Path(root) / "players" / sport).glob("*.json")):
        try:
            games.update(json.loads(f.read_text()))
        except (OSError, ValueError):
            continue
    lines = _lines(root, sport)
    order = sorted((g["date"], gid) for gid, g in games.items() if gid in lines)
    season_of = lambda d: int(d[:4]) if int(d[5:7]) >= 7 else int(d[:4]) - 1  # noqa: E731
    played: dict[tuple, int] = defaultdict(int)  # (season, team, player) -> games
    prod: dict[tuple, float] = defaultdict(float)
    team_games: dict[tuple, int] = defaultdict(int)
    out = []
    for date, gid in order:
        g, ln = games[gid], lines[gid]
        s = season_of(date)
        rows = [_value(sport, r) for r in g.get("players") or g.get("rows") or []]
        here: dict[str, set] = defaultdict(set)
        for team, name, _ in rows:
            here[team].add(name)
        f = {"gid": gid, "date": date, "line": ln, "sides": {}}
        for side in ("home", "away"):
            team = ln[side]
            n = team_games[(s, team)]
            if n < MIN_GAMES:
                f["sides"][side] = None
                continue
            roster = {p: v for (ss, t, p), v in prod.items() if ss == s and t == team and played[(s, t, p)] >= n / 2}
            total = sum(roster.values()) or 1.0
            top = sorted(roster, key=lambda p: -roster[p])[:3]
            missing = [p for p in roster if p not in here[team]]
            f["sides"][side] = {"top_out": bool(top) and top[0] not in here[team],
                                "top3_out": sum(1 for p in top if p not in here[team]),
                                "share_out": sum(roster[p] for p in missing) / total}
        out.append(f)
        for team, name, v in rows:
            played[(s, team, name)] += 1
            prod[(s, team, name)] += v
        for team in here:
            team_games[(s, team)] += 1
    return out


RULES = {
    "back: team missing its top player": lambda me, op: me["top_out"],
    "fade: team missing its top player (bet the opponent)": lambda me, op: op["top_out"],
    "back: team missing 2+ of its top 3": lambda me, op: me["top3_out"] >= 2,
    "fade: team missing 2+ of its top 3 (bet the opponent)": lambda me, op: op["top3_out"] >= 2,
    "back: team missing 25%+ of its production": lambda me, op: me["share_out"] >= 0.25,
    "fade: team missing 25%+ of its production (bet the opponent)": lambda me, op: op["share_out"] >= 0.25,
    "back: full strength vs a team missing its top player": lambda me, op: me["share_out"] < 0.05 and op["top_out"],
}


def _won(margin: float, side: str) -> float:
    m = margin if side == "home" else -margin
    return 1.0 if m > 0 else 0.0 if m < 0 else 0.5


def _cover(ln: dict, side: str) -> float | None:
    if ln["spread"] is None:
        return None
    m = ln["margin"] + ln["spread"]
    m = m if side == "home" else -m
    return 1.0 if m > 0 else 0.0 if m < 0 else 0.5


def run(root, sport: str = "nba", random_n: int = 100, seed: int = 31) -> dict:
    feats = features(root, sport)
    cut = int(len(feats) * TUNE_SHARE)
    rng = random.Random(seed)
    chosen = [[rng.random() < 0.1 for _ in feats] for _ in range(random_n)]
    recs: dict = {}
    control: dict = {}

    def put(book, key, i, price, res):
        arm = "blind" if i >= cut else "in_sample"
        book.setdefault(key, {"in_sample": Record(), "blind": Record()})[arm].add(price, res)

    for i, f in enumerate(feats):
        ln = f["line"]
        for side in ("home", "away"):
            other = "away" if side == "home" else "home"
            me, op = f["sides"][side], f["sides"][other]
            if me is None or op is None:
                continue
            markets = []
            price = ln["ml"][side]
            if price is not None and DOG[0] <= price <= DOG[1]:
                markets.append(("moneyline dog", price, _won(ln["margin"], side)))
            elif price is not None and MAX_FAV <= price < 0:
                markets.append(("moneyline favorite", price, _won(ln["margin"], side)))
            cov = _cover(ln, side) if sport != "nhl" else None
            if cov is not None:
                markets.append(("spread", ln["spread_odds"][side] or SPREAD_PRICE, cov))
            for market, pr, res in markets:
                for k in range(random_n):
                    if chosen[k][i]:
                        put(control, (market, k), i, pr, res)
                for name, rule in RULES.items():
                    if rule(me, op):
                        put(recs, (market, name), i, pr, res)
    rows = [{"study": f"{sport.upper()} absences", "sport": sport, "market": k[0], "variant": k[1],
             "verdict": verdict(r, k[1]), **{a: r[a].to_dict() for a in r}} for k, r in sorted(recs.items())]
    fakes = [verdict(r) for r in control.values()]
    return {"games": len(feats), "tune_games": cut, "rows": rows,
            "control": {"spots": len(fakes), "proven": sum(1 for x in fakes if x), "strong": fakes.count("STRONG")}}


__all__ = ["run", "features", "RULES"]
