"""Tennis player report (owner, Oct 25: "can you do that with tennis as well"): the team reports'
tennis twin, built from the ESPN results and schedule tennis.py already reads (matches.csv).

For every player in the next 36 hours of matches: form (last 10, streak, hot / cold against the
season), the season and this surface's record, the surface-aware rating and its rank on tour,
fatigue (matches and sets in the last 3 and 7 days), health flags (retirements or walkovers in the
last 60 days, a 30+ day layoff), and per match the head to head and the notes that matter.
Written to data/tennis_report.json. Research only: nothing here posts a pick or carries units.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta

from .tennis import SURFACE, _t, _won, norm, ratings
from .tennishist import Params

HORIZON_H = 36
HEALTH_DAYS = 60
LAYOFF_DAYS = 30
SWING = 0.20  # last-10 win rate this far off the season = hot / cold


def _done(r: dict) -> bool:
    return r.get("status") in ("STATUS_FINAL", "STATUS_RETIRED") and r.get("winner") in ("1", "2")


def _walkover(r: dict) -> bool:
    s = (r.get("status") or "").upper()
    return "WALK" in s or "FORFEIT" in s or "W/O" in (r.get("sets1") or "").upper()


def _sets_played(r: dict) -> int:
    return len((r.get("sets1") or "").split())


def profile(rows_by_player: dict[str, list[dict]], p: str, now: datetime, surface: str, rating: float, rank: int | None,
            name: str) -> dict:
    hist = [r for r in rows_by_player.get(p, []) if _t(r["start"]) < now]
    done = [r for r in hist if _done(r)]
    yr = [r for r in done if _t(r["start"]).year == now.year]
    w = sum(_won(r, p) for r in yr)
    on = [r for r in yr if (r.get("surface") or "").lower() == surface.lower()]
    ws = sum(_won(r, p) for r in on)
    last10 = done[-10:]
    w10 = sum(_won(r, p) for r in last10)
    streak, kind = 0, None
    for r in reversed(done):
        won = _won(r, p)
        if kind is None:
            kind = won
        if won != kind:
            break
        streak += 1
    tag = None
    if len(yr) >= 15 and len(last10) == 10:
        season, recent = w / len(yr), w10 / 10
        tag = "hot" if recent - season >= SWING else "cold" if season - recent >= SWING else None
    within = lambda d: [r for r in done if now - _t(r["start"]) <= timedelta(days=d)]  # noqa: E731
    last3, last7 = within(3), within(7)
    recent = [r for r in hist if now - _t(r["start"]) <= timedelta(days=HEALTH_DAYS)]
    rets = [r for r in recent if r.get("status") == "STATUS_RETIRED" and not _won(r, p)]
    wos = [r for r in recent if _walkover(r) and r.get("winner") in ("1", "2") and not _won(r, p)]
    days_off = (now - _t(done[-1]["start"])).total_seconds() / 86400 if done else None
    health = []
    if rets:
        health.append(f"retired from {len(rets)} match{'es' if len(rets) > 1 else ''} in the last {HEALTH_DAYS} days "
                      f"(last {_t(rets[-1]['start']):%-m/%-d})")
    if wos:
        health.append(f"gave a walkover in the last {HEALTH_DAYS} days")
    if days_off is not None and days_off >= LAYOFF_DAYS:
        health.append(f"{days_off:.0f} days since the last match (rust)")
    return {"name": name, "rating": round(rating), "rank": rank, "season": f"{w}-{len(yr) - w}",
            "surface_rec": f"{ws}-{len(on) - ws}" if on else None, "last10": f"{w10}-{len(last10) - w10}" if last10 else None,
            "streak": (f"W{streak}" if kind else f"L{streak}") if streak >= 2 else None, "form": tag,
            "load3": len(last3), "load7": len(last7), "sets7": sum(_sets_played(r) for r in last7),
            "days_off": days_off and round(days_off, 1), "health": health}


def build(rows: list[dict], now: datetime) -> dict:
    by_player: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        if not r.get("start"):
            continue
        for k in ("p1_name", "p2_name"):
            by_player[norm(r.get(k) or "")].append(r)
    for lst in by_player.values():
        lst.sort(key=lambda r: r["start"])
    elo = ratings(sorted(rows, key=lambda r: r.get("start") or ""), Params(k=150.0, surface_weight=0.3), now)
    # Tour ranks by overall rating among players who played in the last year.
    active: dict[str, set] = defaultdict(set)
    for r in rows:
        if _done(r) and now - _t(r["start"]) <= timedelta(days=365):
            active[r["tour"]].update((norm(r["p1_name"]), norm(r["p2_name"])))
    ranks = {}
    for tour, ps in active.items():
        order = sorted(ps, key=lambda p: -elo.rating(tour, p, "Hard"))
        ranks.update({(tour, p): i + 1 for i, p in enumerate(order)})
    matches, players = [], {}
    for r in rows:
        if r.get("status") not in ("STATUS_SCHEDULED", "STATUS_IN_PROGRESS") or not r.get("start"):
            continue
        start = _t(r["start"])
        if not now - timedelta(hours=3) <= start <= now + timedelta(hours=HORIZON_H):
            continue
        a, b = r["p1_name"], r["p2_name"]
        na, nb = norm(a), norm(b)
        if not na or not nb or "tbd" in (na, nb):
            continue
        tour = r["tour"]
        surface = SURFACE.get((r.get("surface") or "hard").lower(), "Hard")
        pa, pb = (profile(by_player, n, now, surface, elo.rating(tour, n, surface), ranks.get((tour, n)), x)
                  for n, x in ((na, a), (nb, b)))
        players[na], players[nb] = pa, pb
        notes = []
        for x in (pa, pb):
            notes += [f"{x['name']}: {h}" for h in x["health"]]
            if x["load3"] >= 3:
                notes.append(f"{x['name']}: {x['load3']} matches in the last 3 days ({x['sets7']} sets this week)")
            if x["form"]:
                notes.append(f"{x['name']} is " + {"cold": "ice cold 🥶", "hot": "cookin' 🔥"}.get(x["form"], x["form"]) + f": {x['last10']} the last 10, {x['season']} on the year")
        if pa["load7"] - pb["load7"] >= 3 or pb["load7"] - pa["load7"] >= 3:
            tired, fresh = (pa, pb) if pa["load7"] > pb["load7"] else (pb, pa)
            notes.append(f"Legs check: {tired['name']} has played {tired['load7']} matches this week, {fresh['name']} only {fresh['load7']}")
        gap = pa["rating"] - pb["rating"]
        if abs(gap) >= 150:
            notes.append(f"{(pa if gap > 0 else pb)['name']} has the edge on {surface.lower()}: {abs(gap)} rating points better")
        h2h = [m for m in by_player.get(na, []) if _done(m) and nb in (norm(m["p1_name"]), norm(m["p2_name"]))
               and _t(m["start"]) < now]
        hw = sum(_won(m, na) for m in h2h)
        matches.append({"tour": tour.upper(), "event": r.get("tourney") or "", "round": r.get("round") or "",
                        "surface": surface, "start": r["start"], "a": pa, "b": pb,
                        "h2h": f"{a} {hw}-{len(h2h) - hw}" if h2h else "first meeting", "notes": notes})
    matches.sort(key=lambda m: m["start"])
    return {"sport": "tennis", "generated_at": now.isoformat(), "matches": matches,
            "hot": sorted((p for p in players.values() if p["form"] == "hot"), key=lambda p: p["name"]),
            "cold": sorted((p for p in players.values() if p["form"] == "cold"), key=lambda p: p["name"]),
            "hurt": [p for p in players.values() if p["health"]]}


__all__ = ["build", "profile"]
