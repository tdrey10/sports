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


def scheduled_pairs(rows: list[dict]) -> set[tuple[str, str]]:
    """Every player pair ESPN lists as scheduled or in progress."""
    return {tuple(sorted((norm(r["p1_name"]), norm(r["p2_name"])))) for r in rows
            if r.get("status") in ("STATUS_SCHEDULED", "STATUS_IN_PROGRESS")}


def _played(rows: list[dict], player: str, before: datetime) -> list[dict]:
    """A player's finished matches before ``before``, oldest first (walkovers left out)."""
    out = []
    for r in rows:
        if r.get("status") not in ("STATUS_FINAL", "STATUS_RETIRED") or r.get("winner") not in ("1", "2"):
            continue
        if not r.get("start") or _t(r["start"]) >= before or player not in (norm(r["p1_name"]), norm(r["p2_name"])):
            continue
        out.append(r)
    return out


def _won(r: dict, player: str) -> bool:
    return (r["winner"] == "1") == (norm(r["p1_name"]) == player)


def _score(r: dict, player: str) -> str:
    s1, s2 = (r.get("sets1") or "").split(), (r.get("sets2") or "").split()
    mine, theirs = (s1, s2) if norm(r["p1_name"]) == player else (s2, s1)
    return " ".join(f"{a}-{b}" for a, b in zip(mine, theirs)) + (" ret." if r.get("status") == "STATUS_RETIRED" else "")


def _days(d: float) -> str:
    n = max(round(d), 0)
    return f"{n} day" if n == 1 else f"{n} days"


def match_facts(rows: list[dict], a: str, b: str, start: datetime, tour: str = "") -> list[str]:
    """Facts about a tennis match from ESPN results: form, streaks, last matches, rest, the
    season and surface records, head to head, ratings, and how each player got into the draw."""
    na, nb = norm(a), norm(b)
    out: list[str] = []
    hist = {na: _played(rows, na, start), nb: _played(rows, nb, start)}
    here = next((r for r in rows if r.get("start") and abs((_t(r["start"]) - start).total_seconds()) < 2 * 86400
                 and {norm(r["p1_name"]), norm(r["p2_name"])} == {na, nb}), None)
    if here and here.get("tourney"):
        surface = (here.get("surface") or "").title()
        out.append(f"{here['tourney']}{' · ' + here['round'] if here.get('round') else ''}"
                   f"{' · ' + surface + ' court' if surface else ''}.")
    form = []
    for name, p in ((a, na), (b, nb)):
        last = hist[p][-10:]
        if last:
            w = sum(_won(r, p) for r in last)
            form.append(f"{name} {w}-{len(last) - w}")
    if form:
        out.append("Last 10 matches: " + "; ".join(form) + ".")
    runs = []
    for name, p in ((a, na), (b, nb)):
        n, kind = 0, None
        for r in reversed(hist[p]):
            w = _won(r, p)
            if kind is None:
                kind = w
            if w != kind:
                break
            n += 1
        if n >= 2:
            runs.append(f"{name} has {'won' if kind else 'lost'} {n} straight")
    if runs:
        out.append("Streaks: " + "; ".join(runs) + ".")
    rest = []
    for name, p in ((a, na), (b, nb)):
        if hist[p]:
            days = (start - _t(hist[p][-1]["start"])).total_seconds() / 86400
            m3 = sum(1 for r in hist[p] if start - _t(r["start"]) <= timedelta(days=3))
            rest.append(f"{name} {m3} match{'es' if m3 != 1 else ''} in the last 3 days ({_days(days)} since the last)"
                        if m3 else f"{name} rested ({_days(days)} since the last match)")
    if rest:
        out.append("Rest: " + "; ".join(rest) + ".")
    if here:
        how = []
        for name, p in ((a, na), (b, nb)):
            q = [r for r in hist[p] if r.get("event") == here.get("event") and "Qualif" in (r.get("round") or "")]
            if q and not _won(q[-1], p):
                how.append(f"{name} got in as a lucky loser (lost the last qualifying round)")
            elif q:
                how.append(f"{name} came through qualifying ({len(q)} wins)")
        if how:
            out.append("Draw: " + "; ".join(how) + ".")
    for name, p in ((a, na), (b, nb)):
        last = hist[p][-3:]
        if last:
            bits = []
            for r in reversed(last):
                opp = r["p2_name"] if norm(r["p1_name"]) == p else r["p1_name"]
                bits.append(f"{'beat' if _won(r, p) else 'lost to'} {opp} {_score(r, p)} ({_t(r['start']):%-m/%-d})")
            out.append(f"{name} last 3: " + "; ".join(bits) + ".")
    season = []
    for name, p in ((a, na), (b, nb)):
        yr = [r for r in hist[p] if _t(r["start"]).year == start.year]
        if yr:
            w = sum(_won(r, p) for r in yr)
            surf = (here or {}).get("surface")
            on = [r for r in yr if surf and r.get("surface") == surf]
            ws = sum(_won(r, p) for r in on)
            season.append(f"{name} {w}-{len(yr) - w}" + (f" ({ws}-{len(on) - ws} on {surf.lower()})" if on else ""))
    if season:
        out.append(f"{start.year} record: " + "; ".join(season) + ".")
    h2h = [r for r in hist[na] if nb in (norm(r["p1_name"]), norm(r["p2_name"]))]
    if h2h:
        w = sum(_won(r, na) for r in h2h)
        last = h2h[-1]
        out.append(f"Head to head: {a} {w}-{len(h2h) - w} (last: {a} {'won' if _won(last, na) else 'lost'} "
                   f"{_score(last, na)}, {_t(last['start']):%-m/%-d/%y}).")
    else:
        out.append("First meeting between these two.")
    if tour:
        elo = ratings(rows, Params(k=150.0, surface_weight=0.3), start)
        surface = SURFACE.get(((here or {}).get("surface") or "hard").lower(), "Hard")
        pa = elo.prob(Match(tour, 0, start.isoformat(), surface, int((here or {}).get("bo") or 3), na, nb, True))
        out.append(f"Our ratings: {a} {pa:.0%} · {b} {1 - pa:.0%} ({elo.played[(tour, na)]} vs "
                   f"{elo.played[(tour, nb)]} rated matches; ratings trail the market in the blind test).")
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


__all__ = ["fetch", "norm", "build", "grade", "run", "scheduled_pairs", "match_facts"]
