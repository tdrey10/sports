from datetime import datetime, timezone

from sportsedge.data.base import Quote
from sportsedge.data.cache import JsonCache
from sportsedge.models.ratings import GameResult
from sportsedge.sports import SPORTS
from sportsedge.study import wind_bets
from sportsedge.weather import WeatherClient, outdoor, venue, wind_at

KICK = datetime(2025, 12, 14, 18, 0, tzinfo=timezone.utc)


def test_venues_and_roofs():
    assert outdoor("nfl", "Buffalo Bills") is not None
    assert outdoor("nfl", "Detroit Lions") is None  # dome
    assert outdoor("mlb", "Houston Astros") is None  # retractable roof: usually closed in bad weather
    assert venue("nfl", "Los Angeles Rams")[2] == "dome"
    assert outdoor("nba", "Boston Celtics") is None


def test_wind_at_uses_kickoff_hour_or_a_neighbour():
    hours = {"2025-12-14T18:00": 21.0, "2025-12-14T20:00": 5.0}
    assert wind_at(hours, KICK) == 21.0
    assert wind_at({"2025-12-14T19:00": 9.0}, KICK) == 9.0
    assert wind_at({}, KICK) is None


class FakeSession:
    def __init__(self):
        self.calls = 0

    def get(self, url, params, timeout):
        self.calls += 1

        class R:
            def raise_for_status(self):
                pass

            def json(self):
                return {"hourly": {"time": ["2025-12-14T18:00"], "wind_speed_10m": [22.5]}}
        return R()


def test_weather_client_caches_history(tmp_path):
    s = FakeSession()
    client = WeatherClient(cache=JsonCache(tmp_path), session=s)
    a = client.history(42.7, -78.8, KICK, KICK)
    b = client.history(42.7, -78.8, KICK, KICK)
    assert a == b == {"2025-12-14T18:00": 22.5} and s.calls == 1


def test_windy_outdoor_games_bet_the_under():
    class Wind:
        def history(self, lat, lon, start, end):
            return {"2025-12-14T18:00": 22.0}

    games = [
        GameResult("w", KICK, "Buffalo Bills", "Miami Dolphins", 13, 10),  # outdoor, windy, under 41.5 wins
        GameResult("d", KICK, "Detroit Lions", "Chicago Bears", 30, 27),  # dome: never a wind bet
    ]
    lines = [Quote("totals", "over", 41.5, "draftkings", -110), Quote("totals", "under", 41.5, "draftkings", -110)]
    bets = wind_bets(SPORTS["nfl"], games, lambda g: lines, Wind(), KICK.replace(day=1), KICK.replace(day=20))
    assert [(k, at, b.event_id, b.side, b.win) for k, at, b in bets] == [("wind_under", "close", "w", "under", 1.0)]
    assert wind_bets(SPORTS["nba"], games, lambda g: lines, Wind(), KICK.replace(day=1), KICK.replace(day=20)) == []
