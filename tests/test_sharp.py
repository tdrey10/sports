from datetime import datetime, timedelta, timezone

from sportsedge.models.ratings import GameResult
from sportsedge.sharp import cards, due, find_value
from sportsedge.sports import SPORTS

NOW = datetime(2026, 10, 9, 19, 5, tzinfo=timezone.utc)
START = NOW + timedelta(hours=5)


def book(key, home_ml, away_ml, home_sp=None, home_sp_price=None, away_sp_price=None):
    markets = [{"key": "h2h", "outcomes": [{"name": "Dallas Cowboys", "price": home_ml},
                                            {"name": "Tampa Bay Buccaneers", "price": away_ml}]}]
    if home_sp is not None:
        markets.append({"key": "spreads", "outcomes": [
            {"name": "Dallas Cowboys", "price": home_sp_price, "point": home_sp},
            {"name": "Tampa Bay Buccaneers", "price": away_sp_price, "point": -home_sp}]})
    return {"key": key, "markets": markets}


EVENT = {"id": "x", "commence_time": START.isoformat().replace("+00:00", "Z"),
         "home_team": "Dallas Cowboys", "away_team": "Tampa Bay Buccaneers",
         "bookmakers": [book("pinnacle", -125, 115, -2.5, -105, -105),
                        book("fanduel", -130, 130, -2.5, -110, -110),  # Bucs +130 vs fair ~+120
                        book("draftkings", -128, 110, -2.5, -112, -108)]}


def test_us_book_beating_pinnacles_fair_price_is_value():
    found = find_value(SPORTS["nfl"], EVENT, NOW)
    best = max(found, key=lambda v: v["ev"])
    assert (best["book"], best["market"], best["side"], best["price"]) == ("fanduel", "h2h", "away", 130)
    assert 0.02 <= best["ev"] < 0.1 and 0.44 < best["fair"] < 0.47
    assert not any(v["book"] == "draftkings" and v["side"] == "away" for v in found)  # +110 is worse than fair


def test_sharp_cards_match_the_espn_game_and_pause_on_bad_clv():
    game = GameResult("401", START, "Dallas Cowboys", "Tampa Bay Buccaneers", None, None, state="pre")
    (c,) = cards(SPORTS["nfl"], [EVENT], [game], NOW)
    assert c.tier == "sharp" and c.pick.event_id == "401" and c.pick.book == "FanDuel" and 0.5 <= c.units <= 1
    assert "Pinnacle" in c.headline
    assert cards(SPORTS["nfl"], [EVENT], [game], NOW, paused=True) == []
    assert cards(SPORTS["nfl"], [EVENT], [], NOW) == []  # no matching ESPN game: not gradeable, not posted


def test_checks_twice_a_day():
    assert due(NOW) and not due(NOW.replace(hour=20)) and not due(NOW.replace(minute=50))
