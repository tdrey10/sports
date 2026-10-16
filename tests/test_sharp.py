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


def test_checks_up_to_four_times_a_day():
    from datetime import datetime, timezone

    day = datetime(2026, 10, 14, 0, 15, tzinfo=timezone.utc)  # a Wednesday
    assert [h for h in range(24) if due(day.replace(hour=h))] == [0, 15, 19, 22]
    assert not due(day.replace(minute=50))


def test_saturday_morning_check_is_core_for_early_college_kickoffs():
    from datetime import datetime, timezone

    from sportsedge.sharp import is_core

    sat = datetime(2026, 10, 10, 15, 15, tzinfo=timezone.utc)  # a Saturday, 8:15 AM Pacific
    assert due(sat) and is_core(sat)
    assert due(sat.replace(day=9)) and not is_core(sat.replace(day=9))  # Friday 8 AM: an extra
    assert is_core(sat.replace(hour=19)) and is_core(sat.replace(day=9, hour=0))


def test_extra_checks_only_when_the_month_can_afford_them():
    from datetime import datetime, timezone

    from sportsedge.sharp import CREDIT_FLOOR, affordable

    now = datetime(2026, 10, 20, 22, 0, tzinfo=timezone.utc)  # 12 days left in October
    need = 4 * 12 * 4  # 4 sports x 12 days x 4 checks
    assert affordable(CREDIT_FLOOR + need, 4, now, core=False)
    assert not affordable(CREDIT_FLOOR + need - 1, 4, now, core=False)  # extras pause first
    assert affordable(CREDIT_FLOOR + 4, 4, now, core=True)  # core checks run to the floor
    assert not affordable(CREDIT_FLOOR + 3, 4, now, core=True)
    assert not affordable(None, 4, now, core=False) and affordable(None, 4, now, core=True)


def test_run_spends_requests_only_on_sports_with_games_soon():
    from datetime import datetime, timedelta, timezone

    from sportsedge.sharp import run

    now = datetime(2026, 10, 14, 19, 5, tzinfo=timezone.utc)

    class Client:
        api_key, remaining, calls = "k", 400.0, []

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
