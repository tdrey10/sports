"""College hoops over/under angles we're watching, no money (owner, Nov 10: "add them").

Tested on the 2025-26 season's closing totals (about 5,100 games), neither beat luck yet, so they post
at 0u and keep their own record here, apart from the ledger (they never count for Grim or the board):
- neutral site: the under went 323-275 (54%), up in both halves of the season;
- our projection 12+ points off the book's total: the projection's side went 59-40 (60%), mostly Nov-Jan.

The projection: each team's possessions a game and points scored and allowed per possession, this season
to date blended with last season's (weight games / (games + 5)), from ESPN team box scores. Pace is
home pace x road pace / league pace; each side scores its offense x the other's defense / league average.
It runs about 4.8 points above the book on average, so that is taken off before comparing.

If one keeps hitting through December, it goes through the blind test for money like any other spot.
"""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

SPORT = "ncaab"
GAP = 12.0  # projection vs the book's total, in points
BIAS = 4.8  # the projection's average run above the closing total on 2025-26
MIN_GAMES = 5  # each team's games this season before its projection counts
PRIOR_WEIGHT = 5.0
FILE = "watch_picks.json"
RULES = {
    "neutral": "Neutral-site under",
    "projection": "Our projection 12+ points off the total",
}


def _season(iso: str) -> int:
    y, m = int(iso[:4]), int(iso[5:7])
    return y if m >= 8 else y - 1


def _poss(b: dict) -> float:
    return b["fga"] - b["oreb"] + b["tov"] + 0.475 * b["fta"]


_KEYS = ("fga", "oreb", "tov", "fta", "pts")


def team_ratings(root, season: int) -> tuple[dict, dict, float, float]:
    """(this season's team totals, last season's per-team (pace, off, def), league pace, league points per
    possession) from the saved ESPN team box scores."""
    box: dict = {}
    for f in sorted((Path(root) / "boxscores" / SPORT).glob("*.json")):
        try:
            box.update(json.loads(f.read_text()))
        except (OSError, ValueError):
            continue
    names = {}
    for f in sorted((Path(root) / "games" / SPORT).glob("*.csv")):
        with f.open() as fh:
            for r in csv.DictReader(fh):
                names[r["event_id"]] = (r["home"], r["away"])
    tot = {s: defaultdict(lambda: [0, 0.0, 0.0, 0.0]) for s in (season - 1, season)}  # games, poss, pf, pa
    lg = {s: [0.0, 0.0, 0] for s in (season - 1, season)}  # poss, points, games
    for eid, b in box.items():
        h, a, d = b.get("home") or {}, b.get("away") or {}, b.get("date") or ""
        if not d or not all(k in h and k in a for k in _KEYS):
            continue
        s = _season(d)
        if s not in tot:
            continue
        p = (_poss(h) + _poss(a)) / 2
        if eid not in names:
            continue
        for team, me, op in ((names[eid][0], h, a), (names[eid][1], a, h)):
            t = tot[s][team]
            t[0] += 1; t[1] += p; t[2] += me["pts"]; t[3] += op["pts"]
        lg[s][0] += p; lg[s][1] += h["pts"] + a["pts"]; lg[s][2] += 1
    ref = lg[season - 1] if lg[season - 1][2] else lg[season]
    lp = ref[0] / ref[2] if ref[2] else 68.0
    lppp = ref[1] / (2 * ref[0]) if ref[0] else 1.03
    prior = {t: (v[1] / v[0], v[2] / v[1], v[3] / v[1]) for t, v in tot[season - 1].items() if v[0] and v[1]}
    return dict(tot[season]), prior, lp, lppp


def project(home: str, away: str, cur: dict, prior: dict, lp: float, lppp: float) -> float | None:
    """Projected total points, or None until both teams have MIN_GAMES this season."""
    est = []
    for t in (home, away):
        g, poss, pf, pa = cur.get(t, (0, 0.0, 0.0, 0.0))
        if g < MIN_GAMES or not poss:
            return None
        now = (poss / g, pf / poss, pa / poss)
        before = prior.get(t, (lp, lppp, lppp))
        w = g / (g + PRIOR_WEIGHT)
        est.append(tuple(w * c + (1 - w) * p for c, p in zip(now, before)))
    (ph, oh, dh), (pa_, oa, da) = est
    pace = ph * pa_ / lp
    return pace * (oh * da / lppp + oa * dh / lppp)


def load(root) -> list[dict]:
    try:
        return json.loads((Path(root) / FILE).read_text())
    except (OSError, ValueError):
        return []


def save(root, picks: list[dict]) -> None:
    (Path(root) / FILE).write_text(json.dumps(picks, indent=1))


def find(scans, now: datetime, root) -> list[dict]:
    """Today's college games in a watch angle: one row per game and angle, at the best price."""
    from .proven import _prices

    out = []
    for scan in scans:
        if scan.sport.key != SPORT or not scan.contexts:
            continue
        prices = _prices(scan)
        cur = prior = None
        for eid, ctx in scan.contexts.items():
            start = ctx.event.commence_time
            if start <= now or start > now + timedelta(hours=30):
                continue
            pr = prices.get(eid, {})
            over, under = pr.get(("totals", "over")), pr.get(("totals", "under"))
            if not over or not under or over.point is None:
                continue
            line = over.point
            hits = []
            if ctx.neutral:
                hits.append(("neutral", "under", None))
            if cur is None:
                cur, prior, lp, lppp = team_ratings(root, _season(start.isoformat()))
            proj = project(ctx.home, ctx.away, cur, prior, lp, lppp)
            if proj is not None and abs(proj - BIAS - line) >= GAP:
                hits.append(("projection", "over" if proj - BIAS > line else "under", round(proj - BIAS, 1)))
            for rule, side, p in hits:
                pick = over if side == "over" else under
                out.append({"id": f"{eid}:{rule}", "rule": rule, "event_id": eid, "start": start.isoformat(),
                            "home": ctx.home, "away": ctx.away, "side": side, "total": pick.point,
                            "odds": pick.american, "book": pick.book, "projection": p,
                            "posted_at": now.isoformat(), "status": "pending", "final": None})
    return out


def grade(picks: list[dict], root) -> None:
    """Settle pending rows from the saved final scores (data/games/ncaab)."""
    pending = {p["event_id"] for p in picks if p["status"] == "pending"}
    if not pending:
        return
    scores = {}
    for f in sorted((Path(root) / "games" / SPORT).glob("*.csv"))[-3:]:
        with f.open() as fh:
            for r in csv.DictReader(fh):
                if r["event_id"] in pending and r.get("home_score") and r.get("away_score"):
                    scores[r["event_id"]] = float(r["home_score"]) + float(r["away_score"])
    for p in picks:
        pts = scores.get(p["event_id"]) if p["status"] == "pending" else None
        if pts is None:
            continue
        p["final"] = pts
        p["status"] = "push" if pts == p["total"] else ("won" if (pts > p["total"]) == (p["side"] == "over") else "lost")


def update(scans, now: datetime, root) -> list[dict]:
    """Add today's new rows (first price seen is kept), grade finished games, save."""
    picks = load(root)
    before = json.dumps(picks)
    have = {p["id"] for p in picks}
    picks += [p for p in find(scans, now, root) if p["id"] not in have]
    grade(picks, root)
    if json.dumps(picks) != before:  # write only on a change (a run with nothing new leaves no file)
        save(root, picks)
    return picks


def summary(picks: list[dict], now: datetime) -> dict:
    """For the page: each angle's record (1u flat at its price) and today's open rows."""
    from .odds import american_to_decimal

    rec = {}
    for rule, name in RULES.items():
        rows = [p for p in picks if p["rule"] == rule]
        won = sum(p["status"] == "won" for p in rows)
        lost = sum(p["status"] == "lost" for p in rows)
        units = sum((american_to_decimal(p["odds"]) - 1) if p["status"] == "won" else -1.0 if p["status"] == "lost"
                    else 0.0 for p in rows)
        rec[rule] = {"name": name, "won": won, "lost": lost, "units": round(units, 2)}
    cutoff = (now - timedelta(hours=12)).isoformat()
    recent = sorted((p for p in picks if p["start"] >= cutoff), key=lambda p: p["start"])
    return {"records": rec, "picks": recent}


__all__ = ["find", "grade", "load", "project", "summary", "team_ratings", "update"]
