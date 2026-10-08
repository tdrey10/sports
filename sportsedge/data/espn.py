"""ESPN public site API: results, schedules and box scores (no key needed)."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

from ..models.props import PlayerGame
from ..models.ratings import GameResult
from ..sports import SportConfig
from .cache import JsonCache

log = logging.getLogger(__name__)

BASE = "https://site.api.espn.com/apis/site/v2/sports"
DAY = 86400


def parse_date(s: str) -> datetime:
    s = s.replace("Z", "+00:00")
    if re.match(r".*T\d\d:\d\d\+", s):  # ESPN sometimes omits seconds
        s = s.replace("+", ":00+", 1)
    return datetime.fromisoformat(s)


class EspnClient:
    def __init__(self, cache: JsonCache | None = None, session: requests.Session | None = None, timeout: float = 20):
        self.cache = cache or JsonCache()
        self.session = session or requests.Session()
        self.session.headers.setdefault("User-Agent", "sportsedge/0.1")
        self.timeout = timeout

    def _get(self, url: str, params: dict[str, Any], ttl: float | None) -> dict:
        key = url + "?" + "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        cached = self.cache.get(key, ttl)
        if cached is not None:
            return cached
        resp = self.session.get(url, params=params, timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json()
        self.cache.set(key, data)
        return data

    # ------------------------------------------------------------------ games
    def scoreboard(self, cfg: SportConfig, start: datetime, end: datetime) -> list[dict]:
        params: dict[str, Any] = {
            "dates": f"{start:%Y%m%d}-{end:%Y%m%d}",
            "limit": 1000,
        }
        if cfg.espn_groups:
            params["groups"] = cfg.espn_groups
        # Past ranges never change; anything touching the last two days is refreshed hourly.
        recent = end > datetime.now(timezone.utc) - timedelta(days=2)
        data = self._get(f"{BASE}/{cfg.espn_path}/scoreboard", params, ttl=3600 if recent else None)
        return data.get("events", [])

    def game_results(self, cfg: SportConfig, start: datetime, end: datetime) -> list[GameResult]:
        out: dict[str, GameResult] = {}
        cursor = start
        # NCAAB/NCAAF have thousands of games, keep chunks small enough for limit=1000.
        step = timedelta(days=7 if cfg.espn_groups else 30)
        while cursor <= end:
            chunk_end = min(cursor + step - timedelta(days=1), end)
            try:
                events = self.scoreboard(cfg, cursor, chunk_end)
            except requests.RequestException as exc:
                log.warning("ESPN scoreboard %s %s failed: %s", cfg.key, cursor.date(), exc)
                events = []
            for ev in events:
                g = _parse_event(ev)
                if g is not None:
                    out[g.event_id] = g
            cursor = chunk_end + timedelta(days=1)
        return sorted(out.values(), key=lambda g: g.date)

    # -------------------------------------------------------------- box scores
    def summary(self, cfg: SportConfig, event_id: str) -> dict:
        return self._get(f"{BASE}/{cfg.espn_path}/summary", {"event": event_id}, ttl=None)

    def player_games(self, cfg: SportConfig, results: list[GameResult], team: str) -> list[PlayerGame]:
        team_games = [g for g in results if g.completed and team in (g.home, g.away)]
        team_games = sorted(team_games, key=lambda g: g.date)[-cfg.prop_games:]
        out: list[PlayerGame] = []
        for g in team_games:
            try:
                summary = self.summary(cfg, g.event_id)
            except requests.RequestException as exc:
                log.warning("ESPN summary %s failed: %s", g.event_id, exc)
                continue
            out.extend(pg for pg in parse_box_score(summary, g.event_id, g.date) if pg.team == team)
        return out


def _parse_event(ev: dict) -> GameResult | None:
    try:
        comp = ev["competitions"][0]
        teams = {c["homeAway"]: c for c in comp["competitors"]}
        home, away = teams["home"], teams["away"]
    except (KeyError, IndexError):
        return None
    completed = bool(ev.get("status", {}).get("type", {}).get("completed"))

    def score(c: dict) -> float | None:
        if not completed:
            return None
        try:
            return float(c.get("score"))
        except (TypeError, ValueError):
            return None

    hs, as_ = score(home), score(away)
    return GameResult(
        event_id=str(ev["id"]),
        date=parse_date(ev["date"]),
        home=home["team"]["displayName"],
        away=away["team"]["displayName"],
        home_score=hs if as_ is not None else None,
        away_score=as_ if hs is not None else None,
        neutral=bool(comp.get("neutralSite", False)),
    )


def _parse_stat(raw: str) -> list[float] | None:
    """'25' -> [25]; '8-15' or '20/31' -> [8, 15]; '--' -> None."""
    raw = str(raw).strip()
    if not raw or raw in {"--", "-", "DNP"}:
        return None
    m = re.fullmatch(r"(-?\d+(?:\.\d+)?)\s*[-/]\s*(\d+(?:\.\d+)?)", raw)
    if m:
        return [float(m.group(1)), float(m.group(2))]
    m = re.fullmatch(r"([+-]?\d+(?:\.\d+)?)%?", raw)
    if m:
        return [float(m.group(1))]
    m = re.fullmatch(r"(\d+):(\d\d)", raw)  # time on ice / minutes
    if m:
        return [int(m.group(1)) + int(m.group(2)) / 60]
    return None


def innings_to_outs(ip: float) -> float:
    whole = int(ip)
    return whole * 3 + round((ip - whole) * 10)


def parse_box_score(summary: dict, event_id: str, date: datetime) -> list[PlayerGame]:
    """Flatten ESPN boxscore.players into PlayerGame rows.

    Stats are stored under both ``LABEL`` and ``group.LABEL`` (e.g. ``YDS``
    and ``passing.YDS``); made/attempt stats like ``3PT`` ("2-5") keep the made
    count under the label and the attempts under ``LABEL_A``.
    """
    rows: dict[tuple[str, str], dict[str, float]] = {}
    for team_block in summary.get("boxscore", {}).get("players", []) or []:
        team = team_block.get("team", {}).get("displayName", "")
        for group in team_block.get("statistics", []) or []:
            gname = (group.get("name") or group.get("type") or "").lower()
            labels = group.get("labels") or []
            keys = group.get("keys") or []
            for ath in group.get("athletes", []) or []:
                if ath.get("didNotPlay"):
                    continue
                name = (ath.get("athlete") or {}).get("displayName")
                vals = ath.get("stats") or []
                if not name or not vals:
                    continue
                stats = rows.setdefault((team, name), {})
                for i, raw in enumerate(vals):
                    parsed = _parse_stat(raw)
                    if parsed is None:
                        continue
                    names = []
                    if i < len(labels):
                        names.append(labels[i])
                    if i < len(keys):
                        names.append(keys[i])
                    for label in names:
                        qualified = f"{gname}.{label}" if gname else label
                        for k in {label, qualified}:
                            if k == label and k in stats and gname:
                                continue  # keep first group's value for bare label
                            stats[k] = parsed[0]
                            if len(parsed) > 1:
                                stats[k + "_A"] = parsed[1]
                        if label == "IP":
                            stats[f"{gname}.IP_OUTS" if gname else "IP_OUTS"] = innings_to_outs(parsed[0])
    out = []
    for (team, name), stats in rows.items():
        minutes = stats.get("MIN")
        if minutes is not None and minutes <= 0:
            continue
        out.append(PlayerGame(event_id=event_id, date=date, player=name, team=team, stats=stats))
    return out
