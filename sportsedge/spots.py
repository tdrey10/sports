"""Early-bet spots: situations that can move a price before the market catches up.

Every spot is a WEIGHT, never a trigger. Each spot keeps its own record in the
studies (tuned and blind, at the opening line and the close). Only a spot that
holds up blind adds anything to the read, and all spots together are capped
(+/-6 win-chance points on a dog, 4 on a favorite). Dogs from +100 to +220 only.

Spots are about teams, so they can be checked before a game: rest, travel
across time zones, the day of the game, and the last result.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from .data.base import NameMatcher
from .models.ratings import GameResult

DOG_RANGE = (100, 220)
MAX_DOG_BUMP = 0.06
MAX_FAVORITE_BUMP = 0.04
EASTERN = ZoneInfo("America/New_York")

# Home time zone of every pro team (ET, CT, MT, PT), for travel spots.
_ZONES = {
    "nfl": {
        "ET": "Buffalo Bills|Miami Dolphins|New England Patriots|New York Jets|Baltimore Ravens|Cincinnati Bengals|"
              "Cleveland Browns|Pittsburgh Steelers|Indianapolis Colts|Jacksonville Jaguars|New York Giants|"
              "Philadelphia Eagles|Washington Commanders|Detroit Lions|Atlanta Falcons|Carolina Panthers|"
              "Tampa Bay Buccaneers",
        "CT": "Houston Texans|Tennessee Titans|Kansas City Chiefs|Dallas Cowboys|Chicago Bears|Green Bay Packers|"
              "Minnesota Vikings|New Orleans Saints",
        "MT": "Denver Broncos|Arizona Cardinals",
        "PT": "Las Vegas Raiders|Los Angeles Chargers|Los Angeles Rams|San Francisco 49ers|Seattle Seahawks",
    },
    "nba": {
        "ET": "Atlanta Hawks|Boston Celtics|Brooklyn Nets|Charlotte Hornets|Cleveland Cavaliers|Detroit Pistons|"
              "Indiana Pacers|Miami Heat|New York Knicks|Orlando Magic|Philadelphia 76ers|Toronto Raptors|"
              "Washington Wizards",
        "CT": "Chicago Bulls|Dallas Mavericks|Houston Rockets|Memphis Grizzlies|Milwaukee Bucks|"
              "Minnesota Timberwolves|New Orleans Pelicans|Oklahoma City Thunder|San Antonio Spurs",
        "MT": "Denver Nuggets|Phoenix Suns|Utah Jazz",
        "PT": "Golden State Warriors|LA Clippers|Los Angeles Lakers|Portland Trail Blazers|Sacramento Kings",
    },
    "nhl": {
        "ET": "Boston Bruins|Buffalo Sabres|Detroit Red Wings|Florida Panthers|Montreal Canadiens|Ottawa Senators|"
              "Tampa Bay Lightning|Toronto Maple Leafs|Carolina Hurricanes|Columbus Blue Jackets|New Jersey Devils|"
              "New York Islanders|New York Rangers|Philadelphia Flyers|Pittsburgh Penguins|Washington Capitals",
        "CT": "Chicago Blackhawks|Dallas Stars|Minnesota Wild|Nashville Predators|St. Louis Blues|Winnipeg Jets",
        "MT": "Colorado Avalanche|Utah Mammoth|Utah Hockey Club|Calgary Flames|Edmonton Oilers",
        "PT": "Anaheim Ducks|Los Angeles Kings|San Jose Sharks|Seattle Kraken|Vancouver Canucks|Vegas Golden Knights",
    },
    "mlb": {
        "ET": "Baltimore Orioles|Boston Red Sox|New York Yankees|Tampa Bay Rays|Toronto Blue Jays|Cleveland Guardians|"
              "Detroit Tigers|Atlanta Braves|Miami Marlins|New York Mets|Philadelphia Phillies|Washington Nationals|"
              "Cincinnati Reds|Pittsburgh Pirates",
        "CT": "Chicago White Sox|Kansas City Royals|Minnesota Twins|Houston Astros|Texas Rangers|Chicago Cubs|"
              "Milwaukee Brewers|St. Louis Cardinals",
        "MT": "Colorado Rockies|Arizona Diamondbacks",
        "PT": "Los Angeles Angels|Athletics|Oakland Athletics|Seattle Mariners|Los Angeles Dodgers|San Diego Padres|"
              "San Francisco Giants",
    },
}
ZONE_HOURS = {"ET": 0, "CT": -1, "MT": -2, "PT": -3}
TEAM_ZONE = {sport: {t: z for z, names in zones.items() for t in names.split("|")} for sport, zones in _ZONES.items()}
_MATCHERS = {sport: NameMatcher(table) for sport, table in TEAM_ZONE.items()}

BLOWOUT = {"nfl": 17, "ncaaf": 21, "nba": 20, "ncaab": 20, "mlb": 7, "nhl": 4}


@dataclass(frozen=True)
class Spot:
    key: str
    label: str
    sports: tuple[str, ...]
    market: str = "h2h"  # h2h spots bet the dog; the totals spot bets the under


SPOTS = {
    s.key: s for s in (
        Spot("bye_dog", "Dog off a bye vs a team that played", ("nfl", "ncaaf")),
        Spot("mnf_dog", "Monday night NFL dog", ("nfl",)),
        Spot("east_traveler_dog", "East Coast team flying West (as the dog)", ("nfl", "nba", "nhl", "mlb")),
        Spot("west_host_dog", "West Coast home dog vs a team flying in from the East", ("nfl", "nba", "nhl", "mlb")),
        Spot("blowout_dog", "Dog that blew someone out last game", ("nfl", "ncaaf", "nba", "ncaab", "mlb", "nhl")),
        Spot("wind_under", "Wind 15+ mph at an outdoor stadium: under", ("nfl", "mlb"), market="totals"),
    )
}


def zone(sport: str, team: str) -> str | None:
    m = _MATCHERS.get(sport)
    name = m.match(team) if m else None
    return TEAM_ZONE[sport][name] if name else None


def _last_margin(results: list[GameResult], team: str, before: datetime) -> float | None:
    prev = [g for g in results if g.completed and team in (g.home, g.away) and g.date < before]
    if not prev:
        return None
    g = max(prev, key=lambda g: g.date)
    return g.home_score - g.away_score if g.home == team else g.away_score - g.home_score


def _rest(results: list[GameResult], team: str, before: datetime) -> float | None:
    prev = [g.date for g in results if team in (g.home, g.away) and g.date < before]
    return (before - max(prev)).total_seconds() / 86400 if prev else None


def team_spots(sport: str, home: str, away: str, start: datetime, results: list[GameResult],
               neutral: bool = False) -> dict[str, list[str]]:
    """{"home": [spot keys], "away": [...]}: spots each side would be in if it is the dog.

    Uses only games before ``start``.
    """
    out: dict[str, list[str]] = {"home": [], "away": []}
    teams = {"home": home, "away": away}
    rest = {s: _rest(results, t, start) for s, t in teams.items()}
    for side, team in teams.items():
        other = "away" if side == "home" else "home"
        if "bye_dog" in _active(sport) and rest[side] is not None and rest[other] is not None \
                and rest[side] >= 12 and rest[other] <= 9:
            out[side].append("bye_dog")
        if "mnf_dog" in _active(sport) and start.astimezone(EASTERN).weekday() == 0:
            out[side].append("mnf_dog")
        margin = _last_margin(results, team, start)
        if "blowout_dog" in _active(sport) and margin is not None and margin >= BLOWOUT.get(sport, 99):
            out[side].append("blowout_dog")
    if not neutral and "east_traveler_dog" in _active(sport):
        hz, az = zone(sport, home), zone(sport, away)
        if hz and az and ZONE_HOURS[az] - ZONE_HOURS[hz] >= 3:  # East Coast visitor, West Coast host
            out["away"].append("east_traveler_dog")
            out["home"].append("west_host_dog")
    return out


def _active(sport: str) -> set[str]:
    return {k for k, s in SPOTS.items() if sport in s.sports and s.market == "h2h"}


def is_dog_price(american: float) -> bool:
    return DOG_RANGE[0] <= american <= DOG_RANGE[1]


def capped_bump(bumps: list[float], american: float) -> float:
    """Total spot weight in win-chance points, capped: +/-6 on a dog, 4 on a favorite."""
    cap = MAX_DOG_BUMP if american > 0 else MAX_FAVORITE_BUMP
    return max(-cap, min(cap, sum(bumps)))
