"""Card facts print game dates in US Eastern, not UTC (bug of 2026-11-23: Seattle U's Sunday-night
win at 02:45 UTC Monday showed as 'Mon 11/23', so it looked like both teams played that day)."""

from datetime import datetime, timezone

from sportsedge.insights import game_day, last_game_line
from sportsedge.models.ratings import GameResult


def test_sunday_night_game_keeps_its_us_date():
    g = GameResult("1", datetime(2026, 11, 23, 2, 45, tzinfo=timezone.utc), "Seattle U", "UC San Diego", 90, 64)
    line = last_game_line([g], "UC San Diego", "UCSD")
    assert "(Sun 11/22)" in line and "11/23" not in line


def test_afternoon_game_and_naive_times():
    assert game_day(datetime(2026, 11, 22, 18, 0, tzinfo=timezone.utc)).day == 22
    assert game_day(datetime(2026, 11, 23, 2, 45)).day == 22  # naive = UTC
    # a TBD game stored as midnight Eastern stays on its own day
    assert game_day(datetime(2026, 11, 24, 5, 0, tzinfo=timezone.utc)).day == 24


def test_plain_date_passes_through():
    from datetime import date
    assert game_day(date(2026, 11, 22)) == date(2026, 11, 22)
