"""Wind at outdoor stadiums, from Open-Meteo (free, no key).

The blueprint: wind of 15+ mph lowers football and baseball totals. Domes and
retractable roofs are skipped (a retractable roof is usually closed in bad
weather). Wind is only a WEIGHT on totals, used once the wind-under spot holds
up in a blind test; until then it is shown and tracked, nothing more.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import requests

from .data.base import NameMatcher
from .data.cache import JsonCache

log = logging.getLogger(__name__)

FORECAST = "https://api.open-meteo.com/v1/forecast"
ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
WINDY_MPH = 15.0

# Home stadium of each team: (latitude, longitude, roof). roof: "open", "dome" or "retractable".
VENUES: dict[str, dict[str, tuple[float, float, str]]] = {
    "nfl": {
        "Buffalo Bills": (42.774, -78.787, "open"), "Miami Dolphins": (25.958, -80.239, "open"),
        "New England Patriots": (42.091, -71.264, "open"), "New York Jets": (40.814, -74.074, "open"),
        "New York Giants": (40.814, -74.074, "open"), "Baltimore Ravens": (39.278, -76.623, "open"),
        "Cincinnati Bengals": (39.095, -84.516, "open"), "Cleveland Browns": (41.506, -81.700, "open"),
        "Pittsburgh Steelers": (40.447, -80.016, "open"), "Indianapolis Colts": (39.760, -86.164, "retractable"),
        "Jacksonville Jaguars": (30.324, -81.637, "open"), "Philadelphia Eagles": (39.901, -75.168, "open"),
        "Washington Commanders": (38.908, -76.864, "open"), "Detroit Lions": (42.340, -83.046, "dome"),
        "Atlanta Falcons": (33.755, -84.401, "retractable"), "Carolina Panthers": (35.226, -80.853, "open"),
        "Tampa Bay Buccaneers": (27.976, -82.503, "open"), "Houston Texans": (29.685, -95.411, "retractable"),
        "Tennessee Titans": (36.166, -86.771, "open"), "Kansas City Chiefs": (39.049, -94.484, "open"),
        "Dallas Cowboys": (32.748, -97.093, "retractable"), "Chicago Bears": (41.862, -87.617, "open"),
        "Green Bay Packers": (44.501, -88.062, "open"), "Minnesota Vikings": (44.974, -93.258, "dome"),
        "New Orleans Saints": (29.951, -90.081, "dome"), "Denver Broncos": (39.744, -105.020, "open"),
        "Arizona Cardinals": (33.528, -112.263, "retractable"), "Las Vegas Raiders": (36.091, -115.184, "dome"),
        "Los Angeles Chargers": (33.953, -118.339, "dome"), "Los Angeles Rams": (33.953, -118.339, "dome"),
        "San Francisco 49ers": (37.403, -121.970, "open"), "Seattle Seahawks": (47.595, -122.332, "open"),
    },
    "mlb": {
        "Baltimore Orioles": (39.284, -76.622, "open"), "Boston Red Sox": (42.346, -71.097, "open"),
        "New York Yankees": (40.829, -73.926, "open"), "Tampa Bay Rays": (27.768, -82.653, "dome"),
        "Toronto Blue Jays": (43.641, -79.389, "retractable"), "Cleveland Guardians": (41.496, -81.685, "open"),
        "Detroit Tigers": (42.339, -83.049, "open"), "Atlanta Braves": (33.891, -84.468, "open"),
        "Miami Marlins": (25.778, -80.220, "retractable"), "New York Mets": (40.757, -73.846, "open"),
        "Philadelphia Phillies": (39.906, -75.166, "open"), "Washington Nationals": (38.873, -77.007, "open"),
        "Cincinnati Reds": (39.097, -84.507, "open"), "Pittsburgh Pirates": (40.447, -80.006, "open"),
        "Chicago White Sox": (41.830, -87.634, "open"), "Kansas City Royals": (39.051, -94.480, "open"),
        "Minnesota Twins": (44.982, -93.278, "open"), "Houston Astros": (29.757, -95.355, "retractable"),
        "Texas Rangers": (32.747, -97.084, "retractable"), "Chicago Cubs": (41.948, -87.656, "open"),
        "Milwaukee Brewers": (43.028, -87.971, "retractable"), "St. Louis Cardinals": (38.623, -90.193, "open"),
        "Colorado Rockies": (39.756, -104.994, "open"), "Arizona Diamondbacks": (33.446, -112.067, "retractable"),
        "Los Angeles Angels": (33.800, -117.883, "open"), "Athletics": (38.580, -121.514, "open"),
        "Seattle Mariners": (47.591, -122.332, "retractable"), "Los Angeles Dodgers": (34.074, -118.240, "open"),
        "San Diego Padres": (32.707, -117.157, "open"), "San Francisco Giants": (37.778, -122.389, "open"),
    },
}
_MATCHERS = {sport: NameMatcher(v) for sport, v in VENUES.items()}


def venue(sport: str, home: str) -> tuple[float, float, str] | None:
    m = _MATCHERS.get(sport)
    name = m.match(home) if m else None
    return VENUES[sport][name] if name else None


def outdoor(sport: str, home: str) -> tuple[float, float] | None:
    v = venue(sport, home)
    return (v[0], v[1]) if v and v[2] == "open" else None


def _hour_key(when: datetime) -> str:
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:00")


class WeatherClient:
    """Hourly wind (mph, 10 m) by stadium. History comes in one request per stadium."""

    def __init__(self, cache: JsonCache | None = None, session: requests.Session | None = None, timeout: float = 20):
        self.cache = cache or JsonCache()
        self.session = session or requests.Session()
        self.timeout = timeout

    def _hourly(self, url: str, params: dict, ttl: float | None) -> dict[str, float]:
        key = "weather:" + url + "?" + "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        cached = self.cache.get(key, ttl)
        if cached is None:
            resp = self.session.get(url, params=params, timeout=self.timeout)
            resp.raise_for_status()
            hourly = resp.json().get("hourly") or {}
            cached = {t: w for t, w in zip(hourly.get("time", []), hourly.get("wind_speed_10m", [])) if w is not None}
            self.cache.set(key, cached)
        return cached

    def forecast_wind(self, lat: float, lon: float, when: datetime) -> float | None:
        """Wind at kickoff for a game in the next two weeks (or the last few days)."""
        try:
            hours = self._hourly(FORECAST, {
                "latitude": lat, "longitude": lon, "hourly": "wind_speed_10m", "wind_speed_unit": "mph",
                "timezone": "UTC", "past_days": 3, "forecast_days": 16,
            }, ttl=3 * 3600)
        except requests.RequestException as exc:
            log.info("weather forecast unavailable: %s", exc)
            return None
        return hours.get(_hour_key(when))

    def history(self, lat: float, lon: float, start: datetime, end: datetime) -> dict[str, float]:
        """Hourly wind for a date range (one request per stadium per study), keyed by UTC hour."""
        end = min(end, datetime.now(timezone.utc) - timedelta(days=6))  # the archive lags a few days
        if end.date() < start.date():
            return {}
        try:
            return self._hourly(ARCHIVE, {
                "latitude": lat, "longitude": lon, "hourly": "wind_speed_10m", "wind_speed_unit": "mph",
                "timezone": "UTC", "start_date": f"{start:%Y-%m-%d}", "end_date": f"{end:%Y-%m-%d}",
            }, ttl=None)
        except requests.RequestException as exc:
            log.info("weather history unavailable: %s", exc)
            return {}


def wind_at(hours: dict[str, float], when: datetime) -> float | None:
    """Wind for the hour of kickoff (or the nearest hour on either side)."""
    for shift in (0, 1, -1):
        w = hours.get(_hour_key(when + timedelta(hours=shift)))
        if w is not None:
            return w
    return None
