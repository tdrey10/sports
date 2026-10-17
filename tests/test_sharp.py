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
    found = find_value(SPORTS["nhl"], EVENT, NOW)
    best = max(found, key=lambda v: v["ev"])
    assert (best["book"], best["market"], best["side"], best["price"]) == ("fanduel", "h2h", "away", 130)
    assert 0.02 <= best["ev"] < 0.1 and 0.44 < best["fair"] < 0.47
    assert not any(v["book"] == "draftkings" and v["side"] == "away" for v in found)  # +110 is worse than fair


def test_sharp_cards_match_the_espn_game_and_pause_on_bad_clv():
    game = GameResult("401", START, "Dallas Cowboys", "Tampa Bay Buccaneers", None, None, state="pre")
    (c,) = cards(SPORTS["nhl"], [EVENT], [game], NOW)
    assert c.tier == "sharp" and c.pick.event_id == "401" and c.pick.book == "FanDuel" and 0.5 <= c.units <= 1
    assert "Pinnacle" in c.headline
    assert cards(SPORTS["nhl"], [EVENT], [game], NOW, paused=True) == []
    assert cards(SPORTS["nhl"], [EVENT], [], NOW) == []  # no matching ESPN game: not gradeable, not posted


def test_every_daytime_run_may_check():
    from datetime import datetime, timezone

    day = datetime(2026, 10, 14, 0, 15, tzinfo=timezone.utc)
    assert [h for h in range(24) if due(day.replace(hour=h))] == [0, 1, 2] + list(range(15, 24))


def test_paced_checks_spread_the_month_over_the_daytime_runs():
    from datetime import datetime, timedelta, timezone

    from sportsedge.sharp import CREDIT_FLOOR, DAY_RUNS, TENNIS_PER_DAY, paced_checks

    now = datetime(2026, 10, 20, 15, 15, tzinfo=timezone.utc)  # 12 days left in October
    spare = 0.5 * 12 * DAY_RUNS  # half a request a run
    left = CREDIT_FLOOR + 12 * TENNIS_PER_DAY + spare
    day = [paced_checks(left, now + timedelta(minutes=30 * i)) for i in range(DAY_RUNS)]
    assert DAY_RUNS // 2 <= sum(day) <= DAY_RUNS // 2 + 1 and max(day) == 1  # about one check an hour
    assert paced_checks(CREDIT_FLOOR + 12 * TENNIS_PER_DAY, now) == 0  # nothing spare: no paced checks
    assert paced_checks(None, now) == 0
    assert paced_checks(CREDIT_FLOOR + 12 * TENNIS_PER_DAY + 4 * 12 * DAY_RUNS, now) == 4


def test_run_spends_requests_only_on_sports_with_games_soon():
    from datetime import datetime, timedelta, timezone

    from sportsedge.sharp import run

    now = datetime(2026, 10, 14, 19, 5, tzinfo=timezone.utc)

    class Client:
        api_key, remaining, calls = "k", 5000.0, []

        def active(self):
            return {SPORTS["nfl"].odds_key, SPORTS["nhl"].odds_key}

        def odds(self, cfg):
            self.calls.append(cfg.key)
            self.remaining -= 1
            return []

    class Provider:
        def game_results(self, cfg, start, end):
            soon = now + timedelta(hours=5 if cfg.key == "nhl" else 72)  # NFL's next game is days away
            return [type("G", (), {"date": soon, "home": "H", "away": "A", "event_id": "1"})()]

    c = Client()
    assert run([SPORTS["nfl"], SPORTS["nhl"]], Provider(), now, client=c) == []
    assert c.calls == ["nhl"]  # no request spent on the NFL today
