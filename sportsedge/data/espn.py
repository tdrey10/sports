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
    # ESPN rejects date ranges (dates=YYYYMMDD-YYYYMMDD returns 400). A single
    # day always works; a whole month (dates=YYYYMM) works for the pro leagues
    # and soccer with limit=1000, but college month queries are truncated, so
    # college sports are fetched one day at a time.
    MONTH_LIMIT = 1000

    def scoreboard(self, cfg: SportConfig, dates: str, final: bool) -> list[dict]:
        """Raw events for one day (YYYYMMDD) or month (YYYYMM). ``final`` periods are cached forever."""
        params: dict[str, Any] = {"dates": dates, "limit": self.MONTH_LIMIT if len(dates) == 6 else 500}
        if cfg.espn_groups:
            params["groups"] = cfg.espn_groups
        data = self._get(f"{BASE}/{cfg.espn_path}/scoreboard", params, ttl=None if final else 600)
        return data.get("events", [])

    def game_results(self, cfg: SportConfig, start: datetime, end: datetime) -> list[GameResult]:
        out: dict[str, GameResult] = {}
        settled_before = datetime.now(timezone.utc) - timedelta(days=2)

        def add(events: list[dict]) -> None:
            for ev in events:
                g = _parse_event(ev)
                if g is not None and start <= g.date <= end + timedelta(days=1):
                    out[g.event_id] = g

        def fetch(dates: str, final: bool) -> list[dict] | None:
            try:
                return self.scoreboard(cfg, dates, final)
            except requests.RequestException as exc:
                log.warning("ESPN scoreboard %s %s failed: %s", cfg.key, dates, exc)
                return None

        def days(first: datetime, last: datetime) -> None:
            d = first
            while d.date() <= last.date():
                events = fetch(f"{d:%Y%m%d}", final=d < settled_before)
                add(events or [])
                d += timedelta(days=1)

        if cfg.espn_groups:  # college: day by day
            days(start, end)
        else:
            month = datetime(start.year, start.month, 1, tzinfo=timezone.utc)
            while month <= end:
                nxt = datetime(month.year + month.month // 12, month.month % 12 + 1, 1, tzinfo=timezone.utc)
                events = fetch(f"{month:%Y%m}", final=nxt < settled_before)
                if events is None or len(events) >= self.MONTH_LIMIT:
                    days(max(month, start), min(nxt - timedelta(days=1), end))  # truncated: go daily
                else:
                    add(events)
                month = nxt
        return sorted(out.values(), key=lambda g: g.date)

    def live_games(self, cfg: SportConfig) -> list[GameResult]:
        """Today's scoreboard, always fresh (used for live betting)."""
        params: dict[str, Any] = {"limit": 500}
        if cfg.espn_groups:
            params["groups"] = cfg.espn_groups
        try:
            resp = self.session.get(f"{BASE}/{cfg.espn_path}/scoreboard", params=params, timeout=self.timeout)
            resp.raise_for_status()
            events = resp.json().get("events", [])
        except (requests.RequestException, ValueError) as exc:
            log.warning("ESPN live scoreboard %s failed: %s", cfg.key, exc)
            return []
        return [g for g in (_parse_event(ev) for ev in events) if g is not None]

    # -------------------------------------------------------------- box scores
    def summary(self, cfg: SportConfig, event_id: str) -> dict:
        return self._get(f"{BASE}/{cfg.espn_path}/summary", {"event": event_id}, ttl=None)

    def box_score(self, cfg: SportConfig, game: GameResult) -> list[PlayerGame]:
        try:
            return parse_box_score(self.summary(cfg, game.event_id), game.event_id, game.date)
        except requests.RequestException as exc:
            log.warning("ESPN summary %s failed: %s", game.event_id, exc)
            return []

    def injuries(self, cfg: SportConfig) -> dict[str, list[tuple[str, str, str]]] | None:
        """team display name -> [(player, status, position)] from ESPN's league injury report.

        None when there is no report (no feed for the sport, or the request failed):
        that means injuries are UNKNOWN, not that everyone is healthy."""
        if cfg.key == "ncaaf":  # ESPN's college report is nearly empty: Covers has every team
            from .covers import college_injuries

            return college_injuries(self.cache, self.session)
        if not cfg.has_injury_feed:
            return None
        try:
            data = self._get(f"{BASE}/{cfg.espn_path}/injuries", {}, ttl=1800)
        except requests.RequestException as exc:
            log.warning("ESPN injuries %s failed: %s", cfg.key, exc)
            return None
        return parse_injuries(data) or None

    def goalie_stats(self, athlete_id: str) -> tuple[float, float] | None:
        """(career save %, career shots faced) of an NHL goalie, or None."""
        if not athlete_id:
            return None
        url = f"https://sports.core.api.espn.com/v2/sports/hockey/leagues/nhl/athletes/{athlete_id}/statistics"
        try:
            data = self._get(url, {}, ttl=24 * 3600)
        except requests.RequestException as exc:
            log.info("ESPN goalie %s stats unavailable: %s", athlete_id, exc)
            return None
        return parse_goalie_stats(data)

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
            out.extend(parse_box_score(summary, g.event_id, g.date))  # both sides: opponents feed matchup factors
        return out


def _midnight_eastern(iso: str) -> bool:
    """No game in our leagues starts at exactly midnight Eastern: that is ESPN's 'time TBD' slot."""
    from zoneinfo import ZoneInfo

    t = parse_date(iso).astimezone(ZoneInfo("America/New_York"))
    return (t.hour, t.minute) == (0, 0)


def _parse_event(ev: dict) -> GameResult | None:
    # Preseason games (season type 1) are mostly backups and say little about real strength.
    season = ev.get("season") or {}
    if season.get("type") == 1 or season.get("slug") == "preseason":
        return None
    try:
        comp = ev["competitions"][0]
        teams = {c["homeAway"]: c for c in comp["competitors"]}
        home, away = teams["home"], teams["away"]
    except (KeyError, IndexError):
        return None
    status = ev.get("status", {}).get("type", {})
    completed = bool(status.get("completed"))
    state = status.get("state", "")

    def score(c: dict) -> float | None:
        try:
            return float(c.get("score"))
        except (TypeError, ValueError):
            return None

    hs, as_ = score(home), score(away)
    hp, hp_era, hp_dec, hp_id = _probable(home)
    ap, ap_era, ap_dec, ap_id = _probable(away)
    final = completed and hs is not None and as_ is not None
    live = state == "in" and hs is not None and as_ is not None
    return GameResult(
        event_id=str(ev["id"]),
        date=parse_date(ev["date"]),
        home=home["team"]["displayName"],
        away=away["team"]["displayName"],
        home_score=hs if final else None,
        away_score=as_ if final else None,
        neutral=bool(comp.get("neutralSite", False)),
        state="post" if completed else state,
        # ESPN marks a game with no start time yet as timeValid false and parks it at midnight Eastern.
        time_tbd=comp.get("timeValid") is False or ev.get("timeValid") is False or _midnight_eastern(ev["date"]),
        detail=status.get("shortDetail") or status.get("detail") or "",
        live_home=hs if live else None,
        live_away=as_ if live else None,
        period=int(ev.get("status", {}).get("period") or 0),
        clock=str(ev.get("status", {}).get("displayClock") or ""),
        home_pitcher=hp,
        home_pitcher_era=hp_era,
        home_pitcher_decisions=hp_dec,
        away_pitcher=ap,
        away_pitcher_era=ap_era,
        away_pitcher_decisions=ap_dec,
        home_starter_id=hp_id,
        away_starter_id=ap_id,
    )


def _probable(competitor: dict) -> tuple[str, float | None, int, str]:
    """(name, ERA, decisions, athlete id) of a team's probable starter (MLB pitcher or NHL goalie)."""
    for prob in competitor.get("probables", []) or []:
        athlete = prob.get("athlete") or {}
        name = athlete.get("displayName") or athlete.get("fullName") or ""
        stats = {}
        for stat in prob.get("statistics", []) or []:
            key = (stat.get("abbreviation") or stat.get("name") or "").upper()
            try:
                stats[key] = float(stat.get("displayValue"))
            except (TypeError, ValueError):
                pass
        if name:
            decisions = int(stats.get("W", 0) + stats.get("L", 0))
            return name, stats.get("ERA"), decisions, str(athlete.get("id") or prob.get("playerId") or "")
    return "", None, 0, ""


def parse_goalie_stats(data: dict) -> tuple[float, float] | None:
    """(save %, shots against) from ESPN's athlete statistics object."""
    stats: dict[str, float] = {}
    for cat in (data.get("splits") or {}).get("categories", []) or []:
        for stat in cat.get("stats", []) or []:
            try:
                stats[stat.get("name", "")] = float(stat.get("value"))
            except (TypeError, ValueError):
                continue
    saves, shots = stats.get("saves"), stats.get("shotsAgainst")
    if saves is not None and shots:
        return saves / shots, shots
    pct = stats.get("savePct")
    if pct and shots:
        return (pct / 100 if pct > 1 else pct), shots
    return None


def parse_injuries(data: dict) -> dict[str, list[tuple[str, str, str]]]:
    """Every team listed in the report, even with no injuries (listed = known)."""
    out: dict[str, list[tuple[str, str, str]]] = {}
    for block in data.get("injuries", []) or []:
        team = block.get("displayName") or (block.get("team") or {}).get("displayName")
        if not team:
            continue
        players = out.setdefault(team, [])
        for item in block.get("injuries", []) or []:
            athlete = item.get("athlete") or {}
            name = athlete.get("displayName")
            status = item.get("status") or (item.get("type") or {}).get("description") or ""
            position = (athlete.get("position") or {}).get("abbreviation") or ""
            if name:
                players.append((name, refine_status(status, item.get("shortComment") or item.get("longComment") or ""), position))
    return out


_CALLS = {
    "Probable": ("probable", "expected to play", "will play", "plans to play", "is available", "upgraded to available",
                 "cleared to play", "good to go", "will suit up", "in the lineup"),
    "Out": ("ruled out", "will not play", "won't play", "will miss", "is out for", "is out against", "will sit", "sidelined for"),
    "Doubtful": ("doubtful",),
    "Questionable": ("questionable", "game-time decision", "uncertain"),
}


def refine_status(status: str, comment: str) -> str:
    """ESPN lists anyone short of fully healthy as 'Day-To-Day'; the call (probable, questionable,
    doubtful, out) is in the written update. Owner, Oct 25: a probable player counts as playing.
    The call named last wins ('upgraded from questionable to probable' -> Probable). An explicit
    status (Out, Doubtful, Probable...) is kept as is."""
    s = (status or "").strip()
    if s.lower() not in ("day-to-day", "questionable", ""):
        return s
    c = (comment or "").lower()
    best, at = None, -1
    for call, words in _CALLS.items():
        for w in words:
            i = c.rfind(w)
            if i > at:
                best, at = call, i
    return best or s


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
