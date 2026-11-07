"""Owner, Nov 7: every lean says what backs it, what to watch out for, and what to stay off."""
from datetime import datetime, timezone

from sportsedge.pickcard import side_notes, stay_off

NOTES = [
    "Seattle Kraken: #1 goalie Philipp Grubauer is playing like a bum 🥶 (saving 0.862 his last 5)",
    "Kraken without Jared McCann (3 G, 5 A): 6% of their ice time and points sit.",
    "Rangers without Sean Durzi (2 G, 3 A): 5% of their ice time and points sit.",
    "New York Rangers: lucky: their record's 7 pts better than their goal differential says, the bill's coming",
    "Seattle Kraken: lost 3 more one-goal games than they won",
    "Mika Zibanejad (New York Rangers) is cookin' 🔥",
]


def test_notes_split_into_for_and_against_the_side_we_picked():
    good, bad = side_notes("Seattle Kraken", "New York Rangers", "home", NOTES)
    assert any("Durzi" in n for n in good) and any("lucky" in n for n in good) and any("one-goal" in n for n in good)
    assert any("Grubauer" in n for n in bad) and any("McCann" in n for n in bad)
    good2, bad2 = side_notes("Seattle Kraken", "New York Rangers", "away", NOTES)
    assert any("Grubauer" in n for n in good2) and any("Durzi" in n for n in bad2)


def test_stay_off_lists_pricey_favorites_on_upcoming_games():
    now = datetime(2026, 11, 7, 8, tzinfo=timezone.utc)
    reports = {"nhl": {"matchups": [
        {"home": "Carolina Hurricanes", "away": "Chicago Blackhawks", "home_ml": -440, "away_ml": 340, "start": "2026-11-08T01:00:00+00:00"},
        {"home": "Seattle Kraken", "away": "New York Rangers", "home_ml": 110, "away_ml": -130, "start": "2026-11-07T22:00:00+00:00"},
        {"home": "Dallas Stars", "away": "San Jose Sharks", "home_ml": -290, "away_ml": 235, "start": "2026-11-01T01:00:00+00:00"}]}}
    assert [x["team"] for x in stay_off(reports, now)] == ["Carolina Hurricanes"]
