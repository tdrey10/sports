"""Does fatigue or form beat the tennis closing line? A blind test on 2016+ matches.

The odds history (tennishist) only has each tournament's start date, so this joins it to the
ESPN results (data/_src/tennis/matches.csv: every match with its real start time, qualifying
included) and measures, before each match, for each player:

- matches played in the last 3 days and days since the last one (fatigue);
- wins in the last 10, and wins above what the ratings expected (form);
- came through qualifying here, or got in as a lucky loser after losing in qualifying;
- career matches (a young player rising fast).

Each spot is bet at the Pinnacle closing price and at Bet365, -170 to +300, tuned on 2016-2019
and judged blind on 2021 on (ESPN has no qualifying rounds before 2021, so those spots are
blind-only). A luck control runs random spots through the same bar.

What it found on the first run: nothing beats the closing line. Backing the rested player
against an opponent who played in the last 3 days lost 10% blind (2,296 bets), because the
"tired" player is usually the one who has been winning; backing the hot player against the
cold one lost 3%. The prices already carry fatigue and form.
"""

from __future__ import annotations

import csv
import gzip
import math
import random
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

from .nflhist import PROVEN_BETS
from .tennis import norm
from .tennishist import Elo, Match, Params

TUNE = (2016, 2019)
BLIND_FIRST = 2021
LO, HI = 1.588, 4.0  # -170 .. +300 in decimal odds
SURFACE = {"hard": "Hard", "clay": "Clay", "grass": "Grass"}


def _t(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def features(matches_csv: Path) -> tuple[list[dict], dict]:
    """ESPN results in time order, and each player's state before each match."""
    rows = []
    with open(matches_csv, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r.get("status") in ("STATUS_FINAL", "STATUS_RETIRED", "STATUS_WALKOVER") and r.get("winner") in ("1", "2") \
                    and r.get("start"):
                rows.append(r)
    rows.sort(key=lambda r: r["start"])
    hist: dict[str, list[tuple]] = defaultdict(list)  # player id -> (time, won, real, event, expected, qualifying)
    elo = Elo(Params(k=150.0, surface_weight=0.3))
    feat = {}
    for r in rows:
        st, q = _t(r["start"]), "Qualif" in (r.get("round") or "")
        a, b = norm(r["p1_name"]), norm(r["p2_name"])
        m = Match(r["tour"], st.year, r["start"], SURFACE.get((r.get("surface") or "").lower(), "Hard"),
                  int(r.get("bo") or 3), a, b, r["winner"] == "1")
        pa = elo.prob(m)
        for pid, exp in ((r["p1"], pa), (r["p2"], 1 - pa)):
            prev = hist[pid]
            last10 = prev[-10:]
            feat[(r["id"], pid)] = {
                "n": len(prev),
                "m3": sum(1 for x in prev if x[2] and st - x[0] <= timedelta(days=3)),
                "days": (st - prev[-1][0]).total_seconds() / 86400 if prev else 99.0,
                "w10": sum(x[1] for x in last10), "n10": len(last10),
                "surp10": sum(x[1] - x[4] for x in last10),
                "qual_here": any(x[3] == r["event"] and x[5] for x in prev),
                "lost_q_here": any(x[3] == r["event"] and x[5] and not x[1] for x in prev),
            }
        won1, real = r["winner"] == "1", r["status"] != "STATUS_WALKOVER"
        hist[r["p1"]].append((st, won1, real, r["event"], pa, q))
        hist[r["p2"]].append((st, not won1, real, r["event"], 1 - pa, q))
        if r["status"] == "STATUS_FINAL":
            elo.update(m)
    return rows, feat


def _hkey(name: str) -> tuple[str, str]:
    """tennis-data names: 'Carreno Busta P.' -> ('carreno busta', 'p')."""
    parts = name.strip().rsplit(" ", 1)
    if len(parts) == 2 and parts[1].replace(".", "").replace("-", "").isalpha() and len(parts[1].rstrip(".")) <= 4:
        return norm(parts[0]), norm(parts[1])[:1]
    return norm(name), ""


def _same(full: str, key: tuple[str, str]) -> bool:
    sur, ini = key
    f = norm(full)
    return (f == sur or f.endswith(" " + sur)) and (not ini or f.startswith(ini))


def join(hist_odds: Path, espn: list[dict], feat: dict) -> list[dict]:
    """Each 2016+ odds-history match matched to its ESPN match (tour, players, winner, dates)."""
    by = defaultdict(list)
    for r in espn:
        if "Qualif" not in (r.get("round") or ""):
            w = r["p1_name"] if r["winner"] == "1" else r["p2_name"]
            by[(r["tour"], norm(w).split(" ")[-1])].append(r)
    out = []
    with gzip.open(hist_odds, "rt", newline="") as fh:
        for h in csv.DictReader(fh):
            if int(h["season"]) < TUNE[0] or (h.get("comment") or "").strip() not in ("Completed", ""):
                continue
            try:
                ps, b3 = (float(h["psw"]), float(h["psl"])), (float(h["b365w"]), float(h["b365l"]))
            except (TypeError, ValueError):
                continue
            if min(*ps, *b3) <= 1:
                continue
            wk, lk = _hkey(h["winner"]), _hkey(h["loser"])
            d0 = datetime.fromisoformat(h["date"] + "T00:00:00+00:00")
            for r in by.get((h["tour"], wk[0].split(" ")[-1]), []):
                if not d0 - timedelta(days=3) <= _t(r["start"]) <= d0 + timedelta(days=16):
                    continue
                w, l, wid, lid = ((r["p1_name"], r["p2_name"], r["p1"], r["p2"]) if r["winner"] == "1"
                                  else (r["p2_name"], r["p1_name"], r["p2"], r["p1"]))
                if _same(w, wk) and _same(l, lk):
                    out.append({"season": int(h["season"]), "W": feat[(r["id"], wid)], "L": feat[(r["id"], lid)],
                                "ps": ps, "b365": b3})
                    break
    return out


def _full(x, o):
    return x["n10"] >= 8 and o["n10"] >= 8


SPOTS = {
    "rested vs opponent with 2+ matches in 3 days": lambda x, o: o["m3"] >= 2 and x["m3"] == 0,
    "rested 3+ days vs opponent who played in the last 3": lambda x, o: o["m3"] >= 1 and x["days"] >= 3,
    "match-sharp (played in the last 3 days) vs opponent rested 3+": lambda x, o: x["m3"] >= 1 and o["days"] >= 3,
    "direct entry vs a qualifier": lambda x, o: o["qual_here"] and not x["qual_here"],
    "the qualifier vs a direct entry": lambda x, o: x["qual_here"] and not o["qual_here"],
    "against a lucky loser": lambda x, o: o["lost_q_here"] and not x["qual_here"],
    "hot (7+ of last 10) vs cold (4 or fewer)": lambda x, o: _full(x, o) and x["w10"] >= 7 and o["w10"] <= 4,
    "form 3+ wins better than ratings expected, vs opponent": lambda x, o: _full(x, o) and x["surp10"] - o["surp10"] >= 3,
    "rising player (<60 matches, 7+ of last 10)": lambda x, o: x["n"] < 60 and x["n10"] >= 8 and x["w10"] >= 7
    and o["n"] >= 100,
    "hot vs cold, opponent played in the last 3 days": lambda x, o: _full(x, o) and x["w10"] >= 7 and o["w10"] <= 4
    and o["m3"] >= 1,
}


def _record(bets: list[float]) -> dict:
    n = len(bets)
    if n < 2:
        return {"bets": n, "roi": None, "se": None}
    m = sum(bets) / n
    se = math.sqrt(sum((b - m) ** 2 for b in bets) / (n - 1) / n)
    return {"bets": n, "roi": round(m, 4), "se": round(se, 4)}


def _bets(fn, book: str, rows: list[dict]) -> dict[str, list[float]]:
    out = {"in_sample": [], "blind": []}
    for r in rows:
        per = "in_sample" if TUNE[0] <= r["season"] <= TUNE[1] else "blind" if r["season"] >= BLIND_FIRST else None
        if per is None:
            continue
        for won, x, o, d in ((True, r["W"], r["L"], r[book][0]), (False, r["L"], r["W"], r[book][1])):
            if LO <= d <= HI and fn(x, o):
                out[per].append(d - 1 if won else -1.0)
    return out


def proven(t: dict, b: dict) -> bool:
    """The blueprint bar; spots with no tuning seasons (qualifying) must also clear it twice over blind."""
    if b["bets"] < PROVEN_BETS or b["roi"] is None or b["roi"] - b["se"] <= 0:
        return False
    if t["bets"] < 30 or t["roi"] is None:
        return b["roi"] - 2 * b["se"] > 0
    return t["roi"] > 0


def run(matches_csv: Path, hist_odds: Path, random_n: int = 100, seed: int = 7) -> dict:
    espn, feat = features(matches_csv)
    rows = join(hist_odds, espn, feat)
    out = {"joined": len(rows), "rows": []}
    for name, fn in SPOTS.items():
        for book in ("ps", "b365"):
            o = _bets(fn, book, rows)
            t, b = _record(o["in_sample"]), _record(o["blind"])
            out["rows"].append({"study": name, "book": "Pinnacle close" if book == "ps" else "Bet365",
                                "in_sample": t, "blind": b, "proven": proven(t, b)})
    rng, passes = random.Random(seed), 0
    for _ in range(random_n):
        s = rng.random()
        o = _bets(lambda x, y, s=s: hash((x["n"], y["n"], x["w10"], y["m3"], s)) % 20 == 0, "ps", rows)
        passes += proven(_record(o["in_sample"]), _record(o["blind"]))
    out["control"] = {"random_spots": random_n, "passed": passes}
    return out


def report(res: dict) -> str:
    def f(r):
        return f"{r['bets']} bets {r['roi']:+.1%} (±{r['se']:.1%})" if r["roi"] is not None else f"{r['bets']} bets"

    lines = [f"Tennis fatigue/form test ({res['joined']} matches with real start times and closing odds)"]
    for r in res["rows"]:
        lines.append(f"  {r['study']} @ {r['book']}: tuning {f(r['in_sample'])} | blind {f(r['blind'])}"
                     + ("  PROVEN" if r["proven"] else ""))
    c = res["control"]
    lines.append(f"  luck control: {c['passed']}/{c['random_spots']} random spots pass the same bar")
    return "\n".join(lines)


__all__ = ["features", "join", "run", "report", "SPOTS", "proven"]
