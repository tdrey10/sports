"""Tennis on the board: a prediction for every ATP and WTA match with a line.

Not picks with money: the 2012+ blind test (tennishist) found the Pinnacle price predicts
matches better than our ratings (blind log loss 0.592 vs 0.624), and the model's bets lost
4-5% in both periods. So tennis posts our honest read of each match, graded on the result,
next to the market's price; it gets money only when a bet type proves out.

Sources (free, from the public archive D503therapper/autonomous-crypto-engine, refreshed
hourly there): data/sports/tennis/odds.json (Bovada moneylines and game spreads for
upcoming matches) and data/sports/tennis/matches.csv (ESPN results and schedule since 2016,
with surface and best-of). The ratings are the blind test's surface-aware Elo, at the
settings it chose, replayed over every ESPN result.
"""

from __future__ import annotations

import csv
import json
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from .forecasts import Forecast
from .odds import no_vig
from .tennishist import Elo, Match, Params

RAW = "https://raw.githubusercontent.com/D503therapper/autonomous-crypto-engine/main/data/sports/tennis/"
FILES = ("odds.json", "matches.csv")
TOURS = {"atp": "ATP", "wta": "WTA"}
SURFACE = {"hard": "Hard", "clay": "Clay", "grass": "Grass", "carpet": "Carpet"}


def fetch(dest: Path, session: requests.Session | None = None) -> Path:
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    s = session or requests.Session()
    for name in FILES:
        r = s.get(RAW + name, timeout=60)
        r.raise_for_status()
        (dest / name).write_bytes(r.content)
    return dest


def norm(name: str) -> str:
    """Accent-free lowercase name with hyphens as spaces: 'Pablo Carreño-Busta' -> 'pablo carreno busta'."""
    s = unicodedata.normalize("NFKD", (name or "").replace("\xa0", " ")).encode("ascii", "ignore").decode()
    return " ".join(s.lower().replace("-", " ").replace(".", " ").split())


def _t(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def read_matches(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    return sorted(rows, key=lambda r: (r.get("start") or "", r.get("id") or ""))


def ratings(rows: list[dict], params: Params, before: datetime) -> Elo:
    """Elo over every finished ESPN match before ``before`` (retirements and walkovers skipped)."""
    elo = Elo(params)
    for r in rows:
        if r.get("status") != "STATUS_FINAL" or r.get("winner") not in ("1", "2"):
            continue
        if not r.get("start") or _t(r["start"]) >= before:
            continue
        a, b = norm(r["p1_name"]), norm(r["p2_name"])
        if not a or not b or "tbd" in (a, b):
            continue
        elo.update(Match(r["tour"], 0, r["start"], SURFACE.get((r.get("surface") or "").lower(), "Hard"),
                         int(r.get("bo") or 3), a, b, r["winner"] == "1"))
    return elo


def _scheduled(rows: list[dict]) -> dict[tuple, dict]:
    """(tour, player, player) -> the ESPN schedule row, for surface, best-of, event and round."""
    out = {}
    for r in rows:
        if r.get("status") in ("STATUS_SCHEDULED", "STATUS_IN_PROGRESS") or r.get("done") == "0":
            a, b = norm(r["p1_name"]), norm(r["p2_name"])
            out[(r["tour"], *sorted((a, b)))] = r
    return out


def _sets(p: float, bo: int) -> tuple[int, int]:
    """Predicted set score for the favorite: a clear favorite in straight sets, otherwise one dropped."""
    need = 3 if bo == 5 else 2
    if p >= 0.68:
        return need, 0
    return need, 1 if need == 2 or p >= 0.58 else 2


def build(odds: dict, rows: list[dict], params: Params, now: datetime, note: str = "") -> list[Forecast]:
    elo = ratings(rows, params, now)
    sched = _scheduled(rows)
    out = []
    for ln in odds.get("lines", []):
        start = _t(ln["start"])
        if start <= now or ln.get("tour") not in TOURS:
            continue
        tour = ln["tour"]
        a, b = ln["a"].replace("\xa0", " ").strip(), ln["b"].replace("\xa0", " ").strip()
        na, nb = norm(a), norm(b)
        row = sched.get((tour, *sorted((na, nb))), {})
        surface = SURFACE.get((row.get("surface") or "hard").lower(), "Hard")
        bo = int(row.get("bo") or 3)
        m = Match(tour, 0, ln["start"], surface, bo, na, nb, True)
        known = elo.played[(tour, na)] >= 10 and elo.played[(tour, nb)] >= 10
        pa = elo.prob(m)
        market = None
        if ln.get("a_ml") and ln.get("b_ml"):
            market = no_vig({"a": float(ln["a_ml"]), "b": float(ln["b_ml"])})
        if not known and market is None:
            continue
        if not known:
            pa = market["a"]  # a player we have too few matches on: the market's read stands in
        winner = "away" if pa >= 0.5 else "home"  # a = away, b = home on the card
        pw = max(pa, 1 - pa)
        ws, ls = _sets(pw, bo)
        away_s, home_s = (ws, ls) if winner == "away" else (ls, ws)
        ra, rb = elo.rating(tour, na, surface), elo.rating(tour, nb, surface)
        facts = []
        if market:
            facts.append(f"Vegas (Bovada): {a} {int(ln['a_ml']):+d} · {b} {int(ln['b_ml']):+d} "
                         f"(market {a} {market['a']:.0%} · {b} {market['b']:.0%}).")
        if ln.get("a_hcp") is not None and ln.get("a_sp"):
            facts.append(f"Game spread: {a} {float(ln['a_hcp']):+g} ({int(ln['a_sp']):+d}).")
        facts.append(f"{surface}-court Elo: {a} {ra:.0f} ({elo.played[(tour, na)]} matches) · "
                     f"{b} {rb:.0f} ({elo.played[(tour, nb)]} matches).")
        if row.get("tourney"):
            facts.append(f"{row['tourney']}{(' · ' + row['round']) if row.get('round') else ''} · best of {bo}.")
        if not known:
            facts.append("Too few matches on one player to rate them: the market's read stands in.")
        if note:
            facts.append(note)
        out.append(Forecast(
            id=f"tennis_{tour}:{na}|{nb}|{start.date().isoformat()}", sport=f"tennis_{tour}",
            sport_name=f"Tennis · {TOURS[tour]}", event_id=row.get("id") or f"{na}|{nb}",
            commence_time=start.isoformat(), home=b, away=a, home_score=float(home_s), away_score=float(away_s),
            p_home=round(1 - pa, 4), p_away=round(pa, 4), winner=winner, model_home=round(1 - pa, 4),
            market_home=round(market["b"], 4) if market else None, facts=facts, updated_at=now.isoformat(),
        ))
    return out


def grade(stored: dict, rows: list[dict], now: datetime) -> int:
    """Grade tennis forecasts on ESPN results (a retirement or walkover voids the prediction)."""
    by_pair: dict[tuple, list[dict]] = {}
    for r in rows:
        key = (r["tour"], *sorted((norm(r["p1_name"]), norm(r["p2_name"]))))
        by_pair.setdefault(key, []).append(r)
    graded = 0
    for f in stored.values():
        if not f.sport.startswith("tennis_") or f.status not in ("pending", "live") or f.start > now:
            continue
        tour = f.sport.split("_", 1)[1]
        na, nb = norm(f.away), norm(f.home)
        near = [r for r in by_pair.get((tour, *sorted((na, nb))), [])
                if r.get("start") and abs((_t(r["start"]) - f.start).total_seconds()) < 3 * 86400]
        if not near:
            if now - f.start > timedelta(days=4):
                f.status, f.graded_at = "void", now.isoformat()
            continue
        r = min(near, key=lambda r: abs((_t(r["start"]) - f.start).total_seconds()))
        if r.get("status") in ("STATUS_RETIRED", "STATUS_WALKOVER", "STATUS_CANCELED", "STATUS_POSTPONED"):
            f.status, f.graded_at, f.facts = "void", now.isoformat(), []
            continue
        if r.get("status") == "STATUS_FINAL" and r.get("winner") in ("1", "2"):
            p1_is_away = norm(r["p1_name"]) == na
            s1 = [int(x) for x in (r.get("sets1") or "").split() if x.isdigit()]
            s2 = [int(x) for x in (r.get("sets2") or "").split() if x.isdigit()]
            sets1 = sum(1 for x, y in zip(s1, s2) if x > y)
            sets2 = sum(1 for x, y in zip(s1, s2) if y > x)
            away_won = (r["winner"] == "1") == p1_is_away
            f.final_away, f.final_home = (sets1, sets2) if p1_is_away else (sets2, sets1)
            f.correct = (f.winner == "away") == away_won
            f.status, f.live, f.graded_at, f.facts = "final", None, now.isoformat(), []
            graded += 1
        elif r.get("status") == "STATUS_IN_PROGRESS":
            f.status = "live"
    return graded


def grade_entries(ledger, rows: list[dict], now: datetime) -> int:
    """Grade tennis picks (sharp value) on ESPN results: sets decide the moneyline; a retirement
    or walkover voids the pick (books differ; void is the safe record)."""
    from .ledger import grade as grade_entry
    from .models.ratings import GameResult

    by_pair: dict[tuple, list[dict]] = {}
    for r in rows:
        by_pair.setdefault(tuple(sorted((norm(r["p1_name"]), norm(r["p2_name"])))), []).append(r)
    graded = 0
    for e in ledger.entries.values():
        if not e.sport.startswith("tennis_") or e.settled or e.start > now:
            continue
        home, away = norm(e.home), norm(e.away)
        near = [r for r in by_pair.get(tuple(sorted((home, away))), [])
                if r.get("start") and abs((_t(r["start"]) - e.start).total_seconds()) < 3 * 86400]
        if not near:
            if now - e.start > timedelta(days=4):
                e.status, e.profit, e.graded_at = "void", 0.0, now.isoformat()
            continue
        r = min(near, key=lambda r: abs((_t(r["start"]) - e.start).total_seconds()))
        if r.get("status") in ("STATUS_RETIRED", "STATUS_WALKOVER", "STATUS_CANCELED", "STATUS_POSTPONED"):
            e.status, e.profit, e.graded_at = "void", 0.0, now.isoformat()
            continue
        if r.get("status") == "STATUS_FINAL" and r.get("winner") in ("1", "2"):
            p1_home = norm(r["p1_name"]) == home
            home_won = (r["winner"] == "1") == p1_home
            game = GameResult(r.get("id", ""), e.start, e.home, e.away, 1.0 if home_won else 0.0,
                              0.0 if home_won else 1.0, state="post")
            grade_entry(e, game, None, now)
            graded += 1
        elif r.get("status") == "STATUS_IN_PROGRESS":
            e.status = "live"
    return graded


def run(ledger, src: Path, study: dict | None, now: datetime | None = None) -> tuple[int, int]:
    """Refresh tennis forecasts in the ledger and grade finished ones. Returns (posted, graded)."""
    from . import forecasts

    now = now or datetime.now(timezone.utc)
    src = Path(src)
    odds = json.loads((src / "odds.json").read_text())
    rows = read_matches(src / "matches.csv")
    p = Params(**(study or {}).get("params", {})) if study else Params(k=150.0, surface_weight=0.3)
    ll = (study or {}).get("log_loss_blind") or {}
    note = (f"Our tennis ratings trail the market blind (log loss {ll['model']:.3f} vs {ll['market']:.3f}): "
            "a read, not a bet.") if ll.get("model") and ll.get("market") else "A read, not a bet."
    fresh = build(odds, rows, p, now, note)
    forecasts.merge(ledger.forecasts, fresh, now)
    graded = grade(ledger.forecasts, rows, now)
    grade_entries(ledger, rows, now)
    return len(fresh), graded


__all__ = ["fetch", "norm", "build", "grade", "run"]
