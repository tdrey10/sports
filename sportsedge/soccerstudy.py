"""Soccer spots, tested blind: form, streaks, head to head, scoring style, draws and rest.

Data: the engine's own ESPN archive (data/games/<league>/YYYY-MM.csv) for the Premier League,
La Liga, Serie A, Bundesliga, Ligue 1, MLS and the Champions League: every result since
October 2024 (for form, streaks and head to head) and closing 3-way moneylines and 2.5-goal
totals from the 2025-26 season on (the prices bet against).

Before each match, from earlier results only: each team's points and goal difference over its
last 5, its current streak, its home or away form, goals scored and allowed per game over the
last 10 (attacking or defensive style), draw rate, days of rest, and the last meetings between
the two. Each spot names a side (home, draw, away, over or under 2.5) and is bet at the
closing price. Matches through TUNE_END tune; later ones are the blind test. A luck control
runs random spots through the same bar. No injury reports exist for soccer, so these spots
are what a soccer pick can stand on.

First run (Oct 2026): see data/soccer_study.json. With only about 1,900 priced matches the
bar of 60+ blind bets is hard to reach; the study reruns every morning as matches are added.
"""

from __future__ import annotations

import csv
import math
import random
from collections import defaultdict, deque
from datetime import datetime
from pathlib import Path

from .nflhist import PROVEN_BETS

LEAGUES = ("epl", "laliga", "seriea", "bundesliga", "ligue1", "mls", "ucl")
TUNE_END = "2026-03-01"
MIN_HISTORY = 5


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _dec(p: float) -> float:
    return 1 + p / 100 if p > 0 else 1 + 100 / -p


def load(folder: Path, leagues=LEAGUES) -> list[dict]:
    rows = []
    for lg in leagues:
        for f in sorted(Path(folder, lg).glob("*.csv")):
            with open(f, newline="", encoding="utf-8") as fh:
                for r in csv.DictReader(fh):
                    hs, as_ = _f(r.get("home_score")), _f(r.get("away_score"))
                    if hs is None or as_ is None or not r.get("date"):
                        continue
                    rows.append({"league": lg, "date": r["date"], "home": r["home"], "away": r["away"],
                                 "hs": hs, "as": as_, "neutral": r.get("neutral") == "1",
                                 "ml": {"home": _f(r.get("close_home_ml")), "draw": _f(r.get("close_draw_ml")),
                                        "away": _f(r.get("close_away_ml"))},
                                 "total": _f(r.get("close_total")),
                                 "ou": {"over": _f(r.get("close_over_price")), "under": _f(r.get("close_under_price"))}})
    seen, out = set(), []
    for r in sorted(rows, key=lambda r: r["date"]):
        key = (r["date"][:10], r["home"], r["away"])
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out


def annotate(rows: list[dict]) -> None:
    """Each team's state before each match, from earlier results only."""
    hist: dict[str, deque] = defaultdict(lambda: deque(maxlen=20))  # (date, gf, ga, home?)
    h2h: dict[tuple, list] = defaultdict(list)
    for r in rows:
        ctx = {}
        for side, team in (("home", r["home"]), ("away", r["away"])):
            h = list(hist[team])
            last5, last10 = h[-5:], h[-10:]
            pts = sum(3 if g > a else 1 if g == a else 0 for _, g, a, _ in last5)
            streak, kind = 0, None
            for _, g, a, _ in reversed(h):
                k = "W" if g > a else "D" if g == a else "L"
                if kind is None:
                    kind = k
                if k != kind:
                    break
                streak += 1
            venue = [x for x in h if x[3] == (side == "home")][-5:]
            ctx[side] = {
                "n": len(h), "pts5": pts, "gd5": sum(g - a for _, g, a, _ in last5),
                "streak": (kind, streak),
                "unbeaten": next((i for i, (_, g, a, _) in enumerate(reversed(h)) if g < a), len(h)),
                "winless": next((i for i, (_, g, a, _) in enumerate(reversed(h)) if g > a), len(h)),
                "gf10": sum(g for _, g, _, _ in last10) / len(last10) if last10 else None,
                "ga10": sum(a for _, _, a, _ in last10) / len(last10) if last10 else None,
                "draws10": sum(1 for _, g, a, _ in last10 if g == a) / len(last10) if last10 else None,
                "venue_pts": sum(3 if g > a else 1 if g == a else 0 for _, g, a, _ in venue) / len(venue) if venue else None,
                "rest": (datetime.fromisoformat(r["date"]) - datetime.fromisoformat(h[-1][0])).days if h else None,
            }
        meet = h2h[tuple(sorted((r["home"], r["away"])))][-3:]
        ctx["h2h_home_wins"] = sum(1 for m in meet if m == r["home"])
        ctx["h2h_away_wins"] = sum(1 for m in meet if m == r["away"])
        ctx["h2h_n"] = len(meet)
        r["ctx"] = ctx
        hist[r["home"]].append((r["date"], r["hs"], r["as"], True))
        hist[r["away"]].append((r["date"], r["as"], r["hs"], False))
        winner = r["home"] if r["hs"] > r["as"] else r["away"] if r["as"] > r["hs"] else "draw"
        h2h[tuple(sorted((r["home"], r["away"])))].append(winner)


def _ok(r) -> bool:
    c = r["ctx"]
    return c["home"]["n"] >= MIN_HISTORY and c["away"]["n"] >= MIN_HISTORY


def _style(c, side, key):
    return c[side][key] if c[side][key] is not None else 0


SPOTS = {
    "home on a 3+ win streak vs an away team winless in 3+": ("home", lambda c: c["home"]["streak"][0] == "W"
                                                              and c["home"]["streak"][1] >= 3 and c["away"]["winless"] >= 3),
    "away on a 3+ win streak vs a home team winless in 3+": ("away", lambda c: c["away"]["streak"][0] == "W"
                                                             and c["away"]["streak"][1] >= 3 and c["home"]["winless"] >= 3),
    "fade a 4+ game win streak (bet the opponent at home)": ("home", lambda c: c["away"]["streak"][0] == "W" and c["away"]["streak"][1] >= 4),
    "home form 6+ points better over the last 5": ("home", lambda c: c["home"]["pts5"] - c["away"]["pts5"] >= 6),
    "away form 6+ points better over the last 5": ("away", lambda c: c["away"]["pts5"] - c["home"]["pts5"] >= 6),
    "home unbeaten in 6+": ("home", lambda c: c["home"]["unbeaten"] >= 6),
    "away team unbeaten in 6+": ("away", lambda c: c["away"]["unbeaten"] >= 6),
    "both attacking (3.2+ goals a game in their matches): over": ("over", lambda c: _style(c, "home", "gf10") + _style(c, "home", "ga10") >= 3.2
                                                                  and _style(c, "away", "gf10") + _style(c, "away", "ga10") >= 3.2),
    "both tight (2.2 or fewer goals a game): under": ("under", lambda c: _style(c, "home", "gf10") + _style(c, "home", "ga10") <= 2.2
                                                      and _style(c, "away", "gf10") + _style(c, "away", "ga10") <= 2.2),
    "both draw-heavy (35%+ of the last 10): draw": ("draw", lambda c: _style(c, "home", "draws10") >= 0.35 and _style(c, "away", "draws10") >= 0.35),
    "home won the last 2+ meetings": ("home", lambda c: c["h2h_n"] >= 2 and c["h2h_home_wins"] >= 2),
    "away won the last 2+ meetings": ("away", lambda c: c["h2h_n"] >= 2 and c["h2h_away_wins"] >= 2),
    "rested home (5+ days) vs away on 3 or fewer": ("home", lambda c: (c["home"]["rest"] or 0) >= 5 and (c["away"]["rest"] or 99) <= 3),
    "rested away (5+ days) vs home on 3 or fewer": ("away", lambda c: (c["away"]["rest"] or 0) >= 5 and (c["home"]["rest"] or 99) <= 3),
    "strong home form (2.2+ points a home game)": ("home", lambda c: (c["home"]["venue_pts"] or 0) >= 2.2),
    "weak road team (under 0.8 points an away game): home": ("home", lambda c: c["away"]["venue_pts"] is not None and c["away"]["venue_pts"] < 0.8),
}


def _price(r, side):
    return r["ou"][side] if side in ("over", "under") else r["ml"][side]


def _won(r, side) -> float | None:
    if side in ("over", "under"):
        if r["total"] is None:
            return None
        g = r["hs"] + r["as"]
        if g == r["total"]:
            return 0.5
        return 1.0 if (g > r["total"]) == (side == "over") else 0.0
    actual = "home" if r["hs"] > r["as"] else "away" if r["as"] > r["hs"] else "draw"
    return 1.0 if actual == side else 0.0


def _bet(r, side) -> float | None:
    p = _price(r, side)
    w = _won(r, side)
    if p is None or w is None or not -170 <= p <= 400:  # soccer draws and dogs run longer: cap +400
        return None
    return 0.0 if w == 0.5 else (_dec(p) - 1) if w else -1.0


def _record(xs) -> dict:
    n = len(xs)
    if n < 2:
        return {"bets": n, "roi": None, "se": None}
    m = sum(xs) / n
    return {"bets": n, "roi": round(m, 4), "se": round(math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1) / n), 4)}


def proven(t, b) -> bool:
    return (b["bets"] >= PROVEN_BETS and b["roi"] is not None and b["roi"] - b["se"] > 0
            and t["roi"] is not None and t["roi"] > 0)


def _run_spot(rows, side, test):
    out = {"tune": [], "blind": []}
    for r in rows:
        if not _ok(r) or not test(r["ctx"]):
            continue
        x = _bet(r, side)
        if x is not None:
            out["tune" if r["date"] < TUNE_END else "blind"].append(x)
    return _record(out["tune"]), _record(out["blind"])


def run(folder: Path, random_n: int = 200, seed: int = 9) -> dict:
    rows = load(folder)
    annotate(rows)
    priced = [r for r in rows if r["ml"]["home"] is not None]
    res = {"matches": len(rows), "priced": len(priced), "rows": []}
    for name, (side, test) in SPOTS.items():
        t, b = _run_spot(priced, side, test)
        res["rows"].append({"spot": name, "side": side, "in_sample": t, "blind": b, "proven": proven(t, b)})
    rng = random.Random(seed)
    passed = 0
    for _ in range(random_n):
        side = rng.choice(["home", "draw", "away", "over", "under"])
        frac = rng.uniform(0.05, 0.3)
        keys = {id(r) for r in priced if rng.random() < frac}
        sel = [r for r in priced if id(r) in keys]
        t, b = _run_spot(sel, side, lambda c: True)
        passed += proven(t, b)
    res["control"] = {"random_spots": random_n, "passed": passed}
    return res


def report(res: dict) -> str:
    def f(r):
        return f"{r['bets']} bets {r['roi']:+.1%} (±{r['se']:.1%})" if r["roi"] is not None else f"{r['bets']} bets"

    lines = [f"Soccer spots ({res['matches']} results for form and history, {res['priced']} with closing prices)"]
    for r in res["rows"]:
        lines.append(f"  {r['spot']}: tuning {f(r['in_sample'])} | blind {f(r['blind'])}" + ("  PROVEN" if r["proven"] else ""))
    c = res["control"]
    lines.append(f"  luck control: {c['passed']}/{c['random_spots']} random spots pass the same bar")
    return "\n".join(lines)


__all__ = ["load", "annotate", "run", "report", "SPOTS", "proven"]
