"""Advanced team stats from ESPN box scores: efficiency, pace, shot share, yards per play.

Each finished game's box score (about half a megabyte) is boiled down to a few raw
counts per team and saved in data/boxscores/<sport>/<YYYY-MM>.json, so each game
is downloaded once. From those counts every team gets rolling (last 10 games,
before the game in question) advanced metrics:

- NBA: net rating (points per 100 possessions for minus against), effective FG%,
  turnover rate, offensive rebound rate, free-throw rate; pace for totals.
- NHL: shot share, power play and penalty kill, faceoffs, save % and shooting %;
  shots per game for totals.
- NFL: yards per play for and against, turnover margin, third-down rate; plays per
  game for totals.
- Soccer: shot share, shots-on-target share, possession; shots on target for totals.

They feed the learned model (learn.py) as extra features, judged blind like the rest.
"""

from __future__ import annotations

import bisect
import json
import logging
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import requests

from .data.espn import BASE
from .models.ratings import GameResult
from .sports import SportConfig

log = logging.getLogger(__name__)

SLOTS = 5  # matchup metrics per sport (side minus opponent), plus one for totals
WINDOW = 10

# ESPN stat name -> our short key (pairs "made-attempted" split into two keys)
_SPLIT = {
    "fieldGoalsMade-fieldGoalsAttempted": ("fgm", "fga"),
    "threePointFieldGoalsMade-threePointFieldGoalsAttempted": ("tpm", "tpa"),
    "freeThrowsMade-freeThrowsAttempted": ("ftm", "fta"),
    "thirdDownEff": ("td_made", "td_att"),
}
_KEEP = {
    "offensiveRebounds": "oreb", "defensiveRebounds": "dreb", "totalTurnovers": "tov", "turnovers": "tov",
    "shotsTotal": "shots", "powerPlayGoals": "ppg", "powerPlayOpportunities": "ppo", "faceoffsWon": "fow",
    "totalOffensivePlays": "plays", "totalYards": "yards",
    "totalShots": "shots", "shotsOnTarget": "sot", "possessionPct": "poss_pct",
}


def _num(v) -> float | None:
    try:
        return float(str(v).replace("%", ""))
    except (TypeError, ValueError):
        return None


def parse_summary(summary: dict) -> dict | None:
    """{"home": {raw counts + pts}, "away": {...}} from an ESPN game summary."""
    teams = (summary.get("boxscore") or {}).get("teams") or []
    comp = ((summary.get("header") or {}).get("competitions") or [{}])[0]
    sides = {}
    for c in comp.get("competitors", []):
        tid = str((c.get("team") or {}).get("id") or c.get("id"))
        sides[tid] = (c.get("homeAway"), _num(c.get("score")))
    out = {}
    for t in teams:
        tid = str((t.get("team") or {}).get("id"))
        where, pts = sides.get(tid, (t.get("homeAway"), None))
        if where not in ("home", "away"):
            continue
        raw: dict[str, float] = {}
        for s in t.get("statistics", []) or []:
            name, val = s.get("name"), s.get("displayValue")
            if name in _SPLIT and isinstance(val, str) and ("-" in val or "/" in val):
                a, b = val.replace("/", "-").split("-")[:2]
                ka, kb = _SPLIT[name]
                if _num(a) is not None and _num(b) is not None:
                    raw[ka], raw[kb] = _num(a), _num(b)
            elif name in _KEEP and _num(val) is not None:
                raw.setdefault(_KEEP[name], _num(val))
        if pts is not None:
            raw["pts"] = pts
        out[where] = raw
    return out if len(out) == 2 and out["home"] and out["away"] else None


def _ratio(a: float, b: float) -> float | None:
    return a / b if b else None


def game_metrics(sport_family: str, own: dict, opp: dict) -> dict[str, float] | None:
    """One team's advanced numbers for one game (None if the box score lacks them)."""
    g = lambda d, k: d.get(k)  # noqa: E731
    try:
        if sport_family == "basketball":
            def poss(d):
                return d["fga"] + 0.44 * d["fta"] - d["oreb"] + d["tov"]
            po, pd = poss(own), poss(opp)
            return {
                "net": 100 * (own["pts"] / po - opp["pts"] / pd),
                "efg": (own["fgm"] + 0.5 * own["tpm"]) / own["fga"] - (opp["fgm"] + 0.5 * opp["tpm"]) / opp["fga"],
                "tov": opp["tov"] / pd - own["tov"] / po,
                "oreb": own["oreb"] / (own["oreb"] + opp["dreb"]) - opp["oreb"] / (opp["oreb"] + own["dreb"]),
                "ftr": own["fta"] / own["fga"] - opp["fta"] / opp["fga"],
                "total": (po + pd) / 2,
            }
        if sport_family == "hockey":
            sf, sa = own["shots"], opp["shots"]
            return {
                "net": sf / (sf + sa) - 0.5,
                "pp": (_ratio(own["ppg"], own["ppo"]) or 0) - (_ratio(opp["ppg"], opp["ppo"]) or 0),
                "fo": own["fow"] / (own["fow"] + opp["fow"]) - 0.5 if own["fow"] + opp["fow"] else 0.0,
                "sv": (1 - opp["pts"] / sa) if sa else 0.9,
                "sh": (own["pts"] / sf) if sf else 0.1,
                "total": sf + sa,
            }
        if sport_family == "football":
            return {
                "net": own["yards"] / own["plays"] - opp["yards"] / opp["plays"],
                "off": own["yards"] / own["plays"],
                "to": opp["tov"] - own["tov"],
                "td": (_ratio(own["td_made"], own["td_att"]) or 0) - (_ratio(opp["td_made"], opp["td_att"]) or 0),
                "plays": own["plays"] - opp["plays"],
                "total": own["plays"] + opp["plays"],
            }
        if sport_family == "soccer":
            sh, osh = own["shots"], opp["shots"]
            st, ost = own["sot"], opp["sot"]
            return {
                "net": st / (st + ost) - 0.5 if st + ost else 0.0,
                "shots": sh / (sh + osh) - 0.5 if sh + osh else 0.0,
                "poss": (own.get("poss_pct") or 50.0) / 100 - 0.5,
                "conv": (own["pts"] / st if st else 0.0) - (opp["pts"] / ost if ost else 0.0),
                "sot": st - ost,
                "total": st + ost,
            }
    except (KeyError, TypeError, ZeroDivisionError):
        return None
    return None


class BoxStore:
    """Saved box-score counts, downloaded once per game within a time budget."""

    def __init__(self, folder, session: requests.Session | None = None, minutes: float | None = None):
        self.folder = Path(folder)
        self.session = session or requests.Session()
        self.deadline = time.monotonic() + minutes * 60 if minutes else None
        self.fetched = 0
        self._data: dict[str, dict[str, dict]] = {}
        self._dirty: set[tuple[str, str]] = set()

    def sport(self, key: str) -> dict[str, dict]:
        if key not in self._data:
            store = {}
            for p in sorted((self.folder / key).glob("*.json")):
                try:
                    store.update(json.loads(p.read_text()))
                except (OSError, ValueError):
                    continue
            self._data[key] = store
        return self._data[key]

    def remaining(self) -> float:
        return float("inf") if self.deadline is None else max(self.deadline - time.monotonic(), 0.0)

    def prefetch(self, cfg: SportConfig, games: list[GameResult], seconds: float | None = None) -> int:
        """Download box scores not saved yet, newest first, until the time budget (or this
        sport's share of it, ``seconds``) runs out."""
        store = self.sport(cfg.key)
        got = 0
        stop = time.monotonic() + seconds if seconds is not None else None
        for g in sorted((g for g in games if g.completed and g.event_id not in store), key=lambda g: g.date,
                        reverse=True):
            if (self.deadline is not None and time.monotonic() > self.deadline) or \
                    (stop is not None and time.monotonic() > stop):
                break
            try:
                r = self.session.get(f"{BASE}/{cfg.espn_path}/summary", params={"event": g.event_id}, timeout=20)
                if r.status_code == 404:
                    parsed = None
                else:
                    r.raise_for_status()
                    parsed = parse_summary(r.json())
            except (requests.RequestException, ValueError) as exc:
                log.info("box score %s %s unavailable: %s", cfg.key, g.event_id, exc)
                continue
            self.fetched += 1
            store[g.event_id] = {"date": g.date.isoformat(), **(parsed or {"none": True})}
            self._dirty.add((cfg.key, f"{g.date:%Y-%m}"))
            got += 1
        return got

    def save(self) -> None:
        for key, month in sorted(self._dirty):
            rows = {eid: v for eid, v in self.sport(key).items() if v.get("date", "")[:7] == month}
            path = self.folder / key / f"{month}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(rows, separators=(",", ":"), sort_keys=True))
        self._dirty.clear()


class Advanced:
    """Rolling advanced metrics per team, from games before the one being priced."""

    def __init__(self, cfg: SportConfig, results: list[GameResult], box: dict[str, dict]):
        self.family = cfg.family if hasattr(cfg, "family") else _family(cfg)
        self.rows: dict[str, list[tuple[datetime, dict]]] = defaultdict(list)
        for g in sorted((g for g in results if g.completed), key=lambda g: g.date):
            b = box.get(g.event_id)
            if not b or "home" not in b:
                continue
            for team, own, opp in ((g.home, b["home"], b["away"]), (g.away, b["away"], b["home"])):
                m = game_metrics(self.family, own, opp)
                if m:
                    self.rows[team].append((g.date, m))
        self.dates = {t: [d for d, _ in r] for t, r in self.rows.items()}

    def before(self, team: str, when: datetime) -> dict[str, float] | None:
        i = bisect.bisect_left(self.dates.get(team, []), when)
        last = [m for _, m in self.rows.get(team, [])[max(0, i - WINDOW):i]]
        if len(last) < 3:
            return None
        return {k: sum(m[k] for m in last) / len(last) for k in last[0]}

    def context(self, g: GameResult) -> dict:
        """Home-minus-away matchup slots and a totals slot; zeros when unknown."""
        h, a = self.before(g.home, g.date), self.before(g.away, g.date)
        if not h or not a:
            return {"adv": [0.0] * SLOTS, "adv_total": 0.0, "adv_known": 0.0}
        keys = [k for k in h if k != "total"][:SLOTS]
        return {"adv": [h[k] - a[k] for k in keys] + [0.0] * (SLOTS - len(keys)),
                "adv_total": (h["total"] + a["total"]) / 2, "adv_known": 1.0}


def _family(cfg: SportConfig) -> str:
    path = cfg.espn_path.split("/")[0]
    return {"basketball": "basketball", "hockey": "hockey", "football": "football", "soccer": "soccer"}.get(path, path)
