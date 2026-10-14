"""Price shopping, tested blind: does the best price across many books beat the consensus?

Data (free, from the public archive D503therapper/autonomous-crypto-engine, data/sports):
odds_history/americanfootball_{nfl,ncaaf}.jsonl.gz holds snapshots of 10-25 sportsbooks'
moneylines for every NFL and college football game since 2020 (no Pinnacle), and
games/{nfl,ncaaf}/YYYY-MM.csv the final scores.

For each game, the snapshot taken a set time before kickoff; the consensus fair chance is the
median no-vig price of every other book; the bet is the single best price that beats it by
the edge threshold (-170 to +300, at most +15%: bigger is a stale or wrong price), one bet
per game. Seasons through 2022 tune, 2023 on are blind. A luck control bets random sides at
the best price on random games, the same number of bets, through the same bar.

First run (Oct 2026): the best price 24h before kickoff at 2%+ over consensus made money blind
in both sports (NFL +40% on 28 bets, college football +40% on 58), but no variant reached 60
blind bets with a tuning profit too: promising, not proven. It reruns every morning as the
archive grows; a variant that passes the proven bar is reported as PROVEN.
"""

from __future__ import annotations

import csv
import gzip
import io
import json
import math
import random
import statistics
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import requests

from .nflhist import PROVEN_BETS

RAW = "https://raw.githubusercontent.com/D503therapper/autonomous-crypto-engine/main/data/sports/"
API = "https://api.github.com/repos/D503therapper/autonomous-crypto-engine/contents/data/sports/games/"
SPORTS = {"nfl": "americanfootball_nfl", "ncaaf": "americanfootball_ncaaf"}
TUNE_LAST = 2022
HOURS = (1, 6, 24)
EDGES = (0.02, 0.03, 0.04)
MAX_EV = 0.15
MIN_BOOKS = 5


def _t(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _dec(p: float) -> float:
    return 1 + p / 100 if p > 0 else 1 + 100 / -p


def _season(d: datetime) -> int:
    return d.year if d.month >= 7 else d.year - 1


def fetch(dest: Path, session: requests.Session | None = None) -> Path:
    """Odds history and 2020+ results for NFL and college football into ``dest``."""
    s = session or requests.Session()
    dest = Path(dest)
    (dest / "results").mkdir(parents=True, exist_ok=True)
    for sport, key in SPORTS.items():
        r = s.get(RAW + f"odds_history/{key}.jsonl.gz", timeout=120)
        r.raise_for_status()
        (dest / f"{key}.jsonl.gz").write_bytes(r.content)
        listing = s.get(API + sport, timeout=60)
        listing.raise_for_status()
        for item in listing.json():
            name = item["name"]
            if not name.endswith(".csv") or name[:4] < "2020":
                continue
            out = dest / "results" / f"{sport}-{name}"
            if out.exists() and name[:7] < datetime.now().strftime("%Y-%m"):
                continue  # past months don't change
            f = s.get(RAW + f"games/{sport}/{name}", timeout=60)
            f.raise_for_status()
            out.write_bytes(f.content)
    return dest


def load_results(folder: Path, sport: str) -> dict:
    by = defaultdict(list)
    for f in sorted(Path(folder).glob(f"results/{sport}-*.csv")):
        with open(f, newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                if r.get("status") != "final" or not r.get("home_score") or not r.get("start"):
                    continue
                st = _t(r["start"])
                by[st.date()].append((st, r["home_name"].lower(), r["away_name"].lower(),
                                      float(r["home_score"]), float(r["away_score"])))
    return by


def _same(full: str, short: str) -> bool:
    return full.startswith(short) or full.endswith(short)


def find(by: dict, start: datetime, home: str, away: str):
    h, a = home.lower(), away.lower()
    for d in (start.date(), start.date() - timedelta(days=1), start.date() + timedelta(days=1)):
        for g in by.get(d, []):
            if abs((g[0] - start).total_seconds()) < 4 * 3600 and _same(h, g[1]) and _same(a, g[2]):
                return g
    return None


def load_odds(path: Path) -> dict:
    ev = defaultdict(list)
    with gzip.open(path, "rt") as fh:
        for line in fh:
            r = json.loads(line)
            if "b" in r:
                ev[r["id"]].append(r)
    return ev


def games(folder: Path, sport: str, hours: int) -> list[dict]:
    """Each game's snapshot ``hours`` before kickoff with its final score."""
    by = load_results(folder, sport)
    out = []
    for snaps in load_odds(Path(folder) / f"{SPORTS[sport]}.jsonl.gz").values():
        ok = [s for s in snaps if _t(s["t"]) - _t(s["snap"]) >= timedelta(hours=hours)]
        if not ok:
            continue
        s = max(ok, key=lambda s: s["snap"])
        g = find(by, _t(s["t"]), s["h"], s["a"])
        if g is None or g[3] == g[4] or len(s["b"]) < MIN_BOOKS:
            continue
        out.append({"season": _season(_t(s["t"])), "books": s["b"], "home_won": g[3] > g[4]})
    return out


def best_bet(game: dict, edge: float):
    """(ev, home side?, price) of the best book beating the others' consensus, or None."""
    fair = {}
    for b, (ph, pa) in game["books"].items():
        qh, qa = 1 / _dec(ph), 1 / _dec(pa)
        fair[b] = qh / (qh + qa)
    best = None
    for b, (ph, pa) in game["books"].items():
        fh = statistics.median(v for k, v in fair.items() if k != b)
        for home, p, f in ((True, ph, fh), (False, pa, 1 - fh)):
            if not -170 <= p <= 300:
                continue
            ev = f * _dec(p) - 1
            if edge <= ev <= MAX_EV and (best is None or ev > best[0]):
                best = (ev, home, p)
    return best


def _record(xs: list[float]) -> dict:
    n = len(xs)
    if n < 2:
        return {"bets": n, "roi": None, "se": None}
    m = sum(xs) / n
    return {"bets": n, "roi": round(m, 4), "se": round(math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1) / n), 4)}


def proven(t: dict, b: dict) -> bool:
    return (b["bets"] >= PROVEN_BETS and b["roi"] is not None and b["roi"] - b["se"] > 0
            and t["roi"] is not None and t["roi"] > 0)


def run(folder: Path, random_n: int = 100, seed: int = 5) -> dict:
    rows, control = [], {"spots": 0, "passed": 0}
    rng = random.Random(seed)
    for sport in SPORTS:
        for hours in HOURS:
            gs = games(folder, sport, hours)
            for edge in EDGES:
                bets = {"tune": [], "blind": []}
                for g in gs:
                    b = best_bet(g, edge)
                    if b:
                        won = g["home_won"] == b[1]
                        bets["tune" if g["season"] <= TUNE_LAST else "blind"].append(_dec(b[2]) - 1 if won else -1.0)
                t, bl = _record(bets["tune"]), _record(bets["blind"])
                rows.append({"sport": sport, "hours": hours, "edge": edge, "games": len(gs),
                             "in_sample": t, "blind": bl, "proven": proven(t, bl)})
            # Luck control: random sides at their best price, as many bets as the 2% variant.
            n_t = sum(1 for g in gs if g["season"] <= TUNE_LAST and best_bet(g, EDGES[0]))
            n_b = sum(1 for g in gs if g["season"] > TUNE_LAST and best_bet(g, EDGES[0]))
            tune_g = [g for g in gs if g["season"] <= TUNE_LAST]
            blind_g = [g for g in gs if g["season"] > TUNE_LAST]
            for _ in range(random_n // (len(SPORTS) * len(HOURS)) or 1):
                rec = {}
                for key, pool, n in (("t", tune_g, n_t), ("b", blind_g, n_b)):
                    xs = []
                    for g in rng.sample(pool, min(n, len(pool))):
                        home = rng.random() < 0.5
                        prices = [v[0] if home else v[1] for v in g["books"].values()]
                        p = max(x for x in prices) if prices else -110
                        if not -170 <= p <= 300:
                            continue
                        xs.append(_dec(p) - 1 if g["home_won"] == home else -1.0)
                    rec[key] = _record(xs)
                control["spots"] += 1
                control["passed"] += proven(rec["t"], rec["b"])
    return {"rows": rows, "control": control, "source": RAW + "odds_history/"}


def report(res: dict) -> str:
    def f(r):
        return f"{r['bets']} bets {r['roi']:+.1%} (±{r['se']:.1%})" if r["roi"] is not None else f"{r['bets']} bets"

    lines = ["Price shopping vs the consensus of other books (NFL and college football moneylines, 2020+)"]
    for r in res["rows"]:
        lines.append(f"  {r['sport'].upper()} {r['hours']}h before, {r['edge']:.0%}+ edge: tuning {f(r['in_sample'])}"
                     f" | blind {f(r['blind'])}" + ("  PROVEN" if r["proven"] else ""))
    c = res["control"]
    lines.append(f"  luck control: {c['passed']}/{c['spots']} random spots pass the same bar")
    return "\n".join(lines)


__all__ = ["fetch", "run", "report", "best_bet", "proven", "games"]
