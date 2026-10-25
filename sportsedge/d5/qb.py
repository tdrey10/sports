"""The QB "key" input (D503 spec section 4) for NFL and college football.

key = (home QB - away QB) / 2, each QB's shrunk adjusted yards per attempt over his last 4 starts:
(yds + 20 TD - 45 INT + 6.0 x 80) / (att + 80). The upcoming QB is the team's most recent starter;
starts count only from this season (July 1 on) and the last 120 days. The starter of a game is the
team's player with the most pass attempts in ESPN's box score.

Box scores come from ESPN, which only the pipeline can reach: ``collect`` saves each finished
game's two starters to data/qb/<sport>.json, a little at a time.
"""

from __future__ import annotations

import csv
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

log = logging.getLogger(__name__)

QB_SPORTS = ("nfl", "ncaaf")
STARTS = 4
WINDOW_DAYS = 120
REASON_KEY = 0.4  # key x s at or above this is a reason: "better QB play lately"


def _path(sport: str, root: Path | str) -> Path:
    return Path(root) / "qb" / f"{sport}.json"


def load_store(sport: str, root: Path | str = "data") -> dict:
    try:
        return json.loads(_path(sport, root).read_text())
    except (OSError, ValueError):
        return {}


def starters(rows) -> dict[str, list]:
    """team -> [qb, yds, td, int, att] for the player with the most pass attempts (PlayerGame rows)."""
    best: dict[str, list] = {}
    for r in rows:
        att = r.stats.get("passing.C/ATT_A")
        if not att:
            continue
        if r.team not in best or att > best[r.team][4]:
            best[r.team] = [r.player, r.stats.get("passing.YDS", 0.0), r.stats.get("passing.TD", 0.0),
                            r.stats.get("passing.INT", 0.0), att]
    return best


def _season_start(now: datetime) -> datetime:
    year = now.year if now.month >= 7 else now.year - 1
    return now.replace(year=year, month=7, day=1, hour=0, minute=0, second=0, microsecond=0)


def collect(sport: str, client, now: datetime, root: Path | str = "data", budget: int = 1500) -> int:
    """Fetch the starters of this season's finished games not yet saved. Returns how many were added."""
    from ..data.espn import parse_box_score
    from ..sports import SPORTS

    store = load_store(sport, root)
    since = _season_start(now)
    todo = []
    for f in sorted((Path(root) / "games" / sport).glob("*.csv")):
        with f.open() as fh:
            for r in csv.DictReader(fh):
                gid = r.get("event_id")
                if not gid or gid in store or not r.get("home_score"):
                    continue
                try:
                    when = datetime.fromisoformat(r["date"])
                except (KeyError, ValueError):
                    continue
                if since <= when < now:
                    todo.append((when, gid))
    added = 0
    for when, gid in sorted(todo, reverse=True)[:budget]:  # newest first: the board needs the latest starts
        try:
            rows = parse_box_score(client.summary(SPORTS[sport], gid), gid, when)
        except Exception as exc:  # one bad box score never stops the rest
            log.info("QB box score %s %s failed: %s", sport, gid, exc)
            continue
        qbs = starters(rows)
        if len(qbs) == 2:
            store[gid] = {"date": when.isoformat(), "teams": qbs}
            added += 1
    if added:
        p = _path(sport, root)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(store, separators=(",", ":"), sort_keys=True))
    return added


class QBIndex:
    """Each team's starts in date order, for key_edge lookups."""

    def __init__(self, store: dict):
        self.starts: dict[str, list[tuple[datetime, str, float, float, float, float]]] = {}
        for g in store.values():
            when = datetime.fromisoformat(g["date"])
            for team, (name, yds, td, ints, att) in g["teams"].items():
                self.starts.setdefault(team, []).append((when, name, yds, td, ints, att))
        for v in self.starts.values():
            v.sort()

    def rating(self, team: str, before: datetime) -> tuple[str, float] | None:
        """(QB, shrunk adjusted yards per attempt) of the team's most recent starter, over his last 4 starts."""
        lo = max(_season_start(before), before - timedelta(days=WINDOW_DAYS))
        mine = [s for s in self.starts.get(team, []) if lo <= s[0] < before]
        if not mine:
            return None
        qb = mine[-1][1]
        his = [s for s in mine if s[1] == qb][-STARTS:]
        yds = sum(s[2] for s in his)
        td = sum(s[3] for s in his)
        ints = sum(s[4] for s in his)
        att = sum(s[5] for s in his)
        return qb, (yds + 20 * td - 45 * ints + 6.0 * 80) / (att + 80)

    def key_edge(self, home: str, away: str, before: datetime) -> float:
        h, a = self.rating(home, before), self.rating(away, before)
        if h is None or a is None:
            return 0.0  # never guess a QB we haven't seen
        return (h[1] - a[1]) / 2


__all__ = ["QB_SPORTS", "QBIndex", "collect", "load_store", "starters", "REASON_KEY"]
