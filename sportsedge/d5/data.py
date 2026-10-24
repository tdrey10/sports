"""Games for the D503 engine: 10 seasons from the ESPN line archive plus this month's results."""

from __future__ import annotations

import csv
import gzip
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

SPORTS = ("nfl", "ncaaf", "nba", "mlb", "nhl", "ncaab")


def _f(v) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x == x else None


def _t(s: str) -> datetime | None:
    if not s:
        return None
    s = s.replace("Z", "+00:00")
    try:
        d = datetime.fromisoformat(s)
    except ValueError:
        try:
            d = datetime.fromisoformat(s[:10] + "T18:00:00+00:00")
        except ValueError:
            return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


@dataclass
class Game:
    sport: str
    gid: str
    start: datetime
    home: str
    away: str
    hs: float | None = None
    as_: float | None = None
    neutral: bool = False
    stype: str = "2"
    ml_h: float | None = None  # closing (last pre-start) moneylines
    ml_a: float | None = None
    open_h: float | None = None
    open_a: float | None = None
    tzo: float | None = None  # venue UTC offset
    elev: float | None = None  # venue elevation, meters
    roof: str = ""
    temp: float | None = None  # F
    wind: float | None = None  # mph
    rain: float | None = None  # mm
    sp_h: str = ""  # MLB starters / NHL goalies when known
    sp_a: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def done(self) -> bool:
        return self.hs is not None and self.as_ is not None

    @property
    def intl(self) -> bool:
        """An overseas game: a neutral site well east of the Americas (London, Germany, Brazil...)."""
        return self.neutral and self.tzo is not None and self.tzo > -3.5


def _from_lines(sport: str, r: dict) -> Game | None:
    start = _t(r.get("start") or r.get("gameday") or "")
    if start is None or not r.get("home_team"):
        return None
    if (r.get("stype") or "2") == "1":
        return None  # preseason
    return Game(
        sport=sport, gid=str(r.get("espn") or r.get("game_id")), start=start,
        home=r["home_team"], away=r["away_team"], hs=_f(r.get("home_score")), as_=_f(r.get("away_score")),
        neutral=r.get("location") == "Neutral", stype=r.get("stype") or "2",
        ml_h=_f(r.get("home_moneyline")), ml_a=_f(r.get("away_moneyline")),
        open_h=_f(r.get("open_home_ml")), open_a=_f(r.get("open_away_ml")),
        tzo=_f(r.get("tzo")), elev=_f(r.get("elev")), roof=r.get("roof") or "",
        temp=_f(r.get("wx_temp") or r.get("temp")), wind=_f(r.get("wx_wind") or r.get("wind")), rain=_f(r.get("wx_rain")),
        sp_h=r.get("sp_home") or "", sp_a=r.get("sp_away") or "",
    )


def _from_results(sport: str, r: dict) -> Game | None:
    start = _t(r.get("date") or "")
    if start is None or not r.get("home"):
        return None
    return Game(
        sport=sport, gid=str(r.get("event_id")), start=start, home=r["home"], away=r["away"],
        hs=_f(r.get("home_score")), as_=_f(r.get("away_score")), neutral=r.get("neutral") in ("1", "True", "true"),
        ml_h=_f(r.get("close_home_ml")), ml_a=_f(r.get("close_away_ml")),
        open_h=_f(r.get("open_home_ml")), open_a=_f(r.get("open_away_ml")),
    )


def load(sport: str, root: Path | str = "data") -> list[Game]:
    """Every game we hold for ``sport``, oldest first, one row per game (the line archive wins)."""
    root = Path(root)
    games: dict[str, Game] = {}
    for f in sorted((root / "lines" / "espn" / sport).glob("*.csv.gz")):
        with gzip.open(f, "rt", newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                g = _from_lines(sport, r)
                if g:
                    games[g.gid] = g
    # The archive names teams by ESPN id ("25"), the results files by name ("Detroit Pistons"):
    # games in both files tie each id to its name, and every game is then keyed by name.
    names: dict[str, str] = {}
    results: list[Game] = []
    for f in sorted((root / "games" / sport).glob("*.csv")):
        with open(f, newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                g = _from_results(sport, r)
                if g is None:
                    continue
                results.append(g)
                old = games.get(g.gid)
                if old is not None:
                    names.setdefault(old.home, g.home)
                    names.setdefault(old.away, g.away)
    for g in games.values():
        g.home, g.away = names.get(g.home, g.home), names.get(g.away, g.away)
    for g in results:
        old = games.get(g.gid)
        if old is None:
            games[g.gid] = g
        else:  # keep the archive's venue / weather, take a newer score or price
            for k in ("hs", "as_", "ml_h", "ml_a", "open_h", "open_a"):
                if getattr(old, k) is None and getattr(g, k) is not None:
                    setattr(old, k, getattr(g, k))
    return sorted((g for g in games.values() if not (_exhibition(g.home) or _exhibition(g.away))),
                  key=lambda g: (g.start, g.gid))


EXHIBITION = {"AFC", "NFC", "American", "National", "EAST", "WEST", "East", "West", "World", "USA", "Canada", "Finland",
              "Sweden", "Atlantic", "Central", "Metro", "Metropolitan", "Pacific"}


def _exhibition(team: str) -> bool:
    """All-star / exhibition sides (never real games)."""
    return team in EXHIBITION or team.startswith("Team ")


__all__ = ["Game", "load", "SPORTS"]
