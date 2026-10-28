"""Injury timing (owner, Oct 28: "I want to win money so whatever helps me get edge").

Do the books move far enough, fast enough, when a player is ruled out? Every run (half-hourly by day)
the injury report is compared with the last one, and each change is logged with the time we saw it:
data/injuries/<sport>/<YYYY-MM>.csv. The game lines are already logged on the same runs
(data/odds/<sport>/<YYYY-MM>.csv, archive.log_snapshots), and the finals with their closes
(data/games). So for every player ruled out before a game we know the line just before the news,
the line when we saw it, the close, and the result.

The study asks:
- How far did the line move on the news (the injured team's no-vig win chance, before vs at the news)?
- Did it keep moving the same way until the close (drift)? If so the books under-reacted at first,
  and a bet on the other team when the news broke beat the close.
- Did that bet win money? Split by time: the first half tunes, the second half is blind.
It gets no units until it passes the blind standard (proven.py); until then it reports "collecting".
"""

from __future__ import annotations

import csv
import json
import math
from datetime import datetime
from pathlib import Path

from .odds import american_to_decimal, no_vig

FIELDS = ["seen_at", "event_id", "start", "team", "side", "player", "pos", "tier", "was", "now"]
BOOK = "draftkings"  # the board's book: one consistent line over time
MIN_BETS = 100  # below this the study is still collecting
KEY_TIERS = ("Key", "Starter")


# --- Logging -------------------------------------------------------------------------------------

def _status(ctx, team: str) -> dict[str, tuple[str, str]]:
    """player -> (status, position) on this team's report now."""
    out = {p: ("out", pos) for p, pos in ctx.injured.get(team, [])}
    for p, pos in ctx.questionable.get(team, []):
        out.setdefault(p, ("questionable", pos))
    return out


def _tiers(sport: str, root) -> dict[str, dict]:
    try:
        from .d5.board import roster_tiers
        return roster_tiers(sport, str(root))
    except Exception:  # no roster report for this sport
        return {}


def log(out_dir: Path, scans, now: datetime, state_path: Path | None = None, root="data") -> int:
    """Write every injury status change since the last run. A team seen for the first time only sets its
    starting point (a player already out then isn't news). Returns how many changes were logged."""
    out_dir = Path(out_dir)
    state_path = Path(state_path or out_dir / "state.json")
    try:
        state = json.loads(state_path.read_text())
    except (OSError, ValueError):
        state = {}
    n = 0
    for scan in scans:
        sport = scan.sport.key
        if not getattr(scan.sport, "has_injury_feed", True) or not scan.contexts:
            continue
        seen = state.setdefault(sport, {})
        tiers = _tiers(sport, root)
        rows = []
        done = set()
        for eid, ctx in sorted(scan.contexts.items(), key=lambda kv: kv[1].event.commence_time):
            if ctx.event.commence_time <= now:
                continue
            for side, team in (("home", ctx.home), ("away", ctx.away)):
                if team in done or team in ctx.injuries_unknown:
                    continue
                done.add(team)  # a team's next game is the one the news is about
                cur = _status(ctx, team)
                before = seen.get(team)
                seen[team] = {p: s for p, (s, _) in cur.items()}
                if before is None:
                    continue
                tier = (tiers.get(team) or {}).get("tiers", {})
                for p in sorted(set(cur) | set(before)):
                    was, now_s = before.get(p, "active"), cur.get(p, ("active", ""))[0]
                    if was != now_s:
                        rows.append({"seen_at": now.isoformat(), "event_id": eid,
                                     "start": ctx.event.commence_time.isoformat(), "team": team, "side": side,
                                     "player": p, "pos": cur.get(p, ("", ""))[1], "tier": tier.get(p, ""),
                                     "was": was, "now": now_s})
        if rows:
            path = out_dir / sport / f"{now:%Y-%m}.csv"
            path.parent.mkdir(parents=True, exist_ok=True)
            new = not path.exists()
            with path.open("a", newline="") as f:
                w = csv.DictWriter(f, fieldnames=FIELDS)
                if new:
                    w.writeheader()
                w.writerows(rows)
            n += len(rows)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, separators=(",", ":"), sort_keys=True))
    return n


# --- The study -----------------------------------------------------------------------------------

def _read(folder: Path) -> list[dict]:
    rows = []
    for path in sorted(Path(folder).glob("*.csv")):
        with path.open() as f:
            rows.extend(csv.DictReader(f))
    return rows


def _ml_path(snaps: list[dict]) -> dict[str, list[tuple[str, float, float, float]]]:
    """event -> [(seen_at, home no-vig chance, home price, away price)] at the board's book, in order."""
    by: dict[tuple[str, str], dict[str, float]] = {}
    for r in snaps:
        if r["market"] == "h2h" and r["book"] == BOOK and r["side"] in ("home", "away") and r["price"]:
            by.setdefault((r["event_id"], r["seen_at"]), {})[r["side"]] = float(r["price"])
    out: dict[str, list] = {}
    for (eid, at), q in sorted(by.items(), key=lambda kv: kv[0][1]):
        if "home" in q and "away" in q:
            out.setdefault(eid, []).append((at, no_vig(q)["home"], q["home"], q["away"]))
    return out


def _finals(folder: Path) -> dict[str, tuple[float, float]]:
    out = {}
    for r in _read(folder):
        try:
            out[r["event_id"]] = (float(r["home_score"]), float(r["away_score"]))
        except (TypeError, ValueError):
            pass
    return out


def _stats(bets: list[dict]) -> dict:
    n = len(bets)
    if not n:
        return {"bets": 0, "flat_roi": None, "se": None, "beat_close": None}
    rets = [b["ret"] for b in bets]
    roi = sum(rets) / n
    sd = math.sqrt(sum((x - roi) ** 2 for x in rets) / (n - 1)) if n > 1 else 0.0
    return {"bets": n, "wins": sum(b["ret"] > 0 for b in bets), "flat_roi": round(roi, 4),
            "se": round(sd / math.sqrt(n), 4), "beat_close": round(sum(b["clv"] > 0 for b in bets) / n, 3)}


def events(sport: str, root="data") -> list[dict]:
    """Each player ruled out before a game, with the line before, at and after the news."""
    root = Path(root)
    paths = _ml_path(_read(root / "odds" / sport))
    finals = _finals(root / "games" / sport)
    out = []
    for c in _read(root / "injuries" / sport):
        if c["now"] != "out" or c["was"] not in ("active", "questionable") or c["seen_at"] >= c["start"]:
            continue
        path = [s for s in paths.get(c["event_id"], []) if s[0] < c["start"]]
        pre = [s for s in path if s[0] < c["seen_at"]]
        at = [s for s in path if s[0] >= c["seen_at"]]
        if not pre or not at:
            continue
        sign = 1 if c["side"] == "home" else -1  # the injured team's chance = sign-adjusted home chance
        p = lambda s: s[1] if sign == 1 else 1 - s[1]  # noqa: E731
        before, news, close = pre[-1], at[0], at[-1]
        other = "away" if c["side"] == "home" else "home"
        price = news[3] if other == "away" else news[2]  # the other team, when we saw the news
        row = {**c, "before": round(p(before), 4), "at_news": round(p(news), 4), "close": round(p(close), 4),
               "news_move": round(p(news) - p(before), 4), "drift": round(p(close) - p(news), 4),
               "bet_price": price,
               # beat the close: the other team's closing no-vig chance is above the break-even of our price
               "clv": round((1 - p(close)) - 1 / american_to_decimal(price), 4)}
        if c["event_id"] in finals:
            hs, as_ = finals[c["event_id"]]
            won = (as_ > hs) if other == "away" else (hs > as_)
            row["ret"] = 0.0 if hs == as_ else (american_to_decimal(price) - 1 if won else -1.0)
        out.append(row)
    # One bet per game and team: the first news counts (later players on the same team are the same move).
    first: dict[tuple[str, str], dict] = {}
    for r in sorted(out, key=lambda r: r["seen_at"]):
        first.setdefault((r["event_id"], r["team"]), r)
    return list(first.values())


def run(sport: str, root="data") -> dict:
    ev = events(sport, root)
    key = [e for e in ev if e["tier"] in KEY_TIERS or (e["pos"] or "").upper() == "QB"]
    res = {"sport": sport, "news": len(ev), "key_news": len(key)}
    for name, rows in (("all", ev), ("key", key)):
        if not rows:
            continue
        drift_same = sum(r["drift"] < 0 for r in rows) / len(rows)  # kept moving against the injured team
        graded = sorted((r for r in rows if "ret" in r), key=lambda r: r["seen_at"])
        half = len(graded) // 2
        tune, blind = _stats(graded[:half]), _stats(graded[half:])
        verdict = "collecting" if len(graded) < MIN_BETS else ""
        if not verdict and blind["flat_roi"] - blind["se"] > 0 and (tune["flat_roi"] or 0) > 0:
            verdict = "STRONG" if tune["flat_roi"] - tune["se"] > 0 else "PROVEN"
        res[name] = {"n": len(rows), "avg_news_move": round(sum(r["news_move"] for r in rows) / len(rows), 4),
                     "avg_drift": round(sum(r["drift"] for r in rows) / len(rows), 4),
                     "drift_same_way": round(drift_same, 3), "graded": len(graded),
                     "in_sample": tune, "blind": blind, "all_bets": _stats(graded), "verdict": verdict or "no edge"}
    return res


def report(res: dict) -> str:
    s = res["sport"].upper()
    if not res.get("all"):
        return f"{s} injury timing: no ruled-out news matched to lines yet (collecting)."
    lines = [f"{s} injury timing: {res['news']} players ruled out before a game ({res['key_news']} key players)."]
    for name in ("all", "key"):
        r = res.get(name)
        if not r:
            continue
        b = r["all_bets"]
        rec = "" if not b["bets"] else (f"; betting the other team at the news: {b['bets']} bets {b['flat_roi']:+.1%}, "
                                        f"beat the close {b['beat_close']:.0%}")
        lines.append(f"  {name}: line moved {r['avg_news_move'] * 100:+.1f} pts on the news, then {r['avg_drift'] * 100:+.1f} "
                     f"more by the close ({r['drift_same_way']:.0%} kept moving the same way){rec}. Verdict: {r['verdict']}.")
    return "\n".join(lines)


SPORTS = ("nfl", "ncaaf", "nba", "nhl", "mlb", "ncaab")

__all__ = ["events", "log", "report", "run"]
