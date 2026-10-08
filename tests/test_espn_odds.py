from datetime import datetime, timedelta, timezone

from sportsedge.data.espn_odds import ESTIMATED_BOOK, EspnOdds, parse_provider_odds, prop_market
from sportsedge.models.ratings import GameResult
from sportsedge.sports import SPORTS

# Shapes copied from ESPN core odds responses (trimmed).
DK_NFL = {
    "provider": {"id": "100", "name": "Draft Kings"},
    "details": "DAL -8.5", "overUnder": 47.5, "spread": -8.5, "overOdds": -115.0, "underOdds": -105.0,
    "homeTeamOdds": {"favorite": True, "moneyLine": -470, "spreadOdds": -115.0},
    "awayTeamOdds": {"favorite": False, "moneyLine": 360, "spreadOdds": -105.0},
}
DK_NHL_HOME_DOG = {
    "provider": {"id": "100", "name": "DraftKings"},
    "details": "EDM -135", "overUnder": 6.5, "spread": 1.5, "overOdds": -148.0, "underOdds": 124.0,
    "homeTeamOdds": {"moneyLine": 114, "spreadOdds": -218.0},
    "awayTeamOdds": {"moneyLine": -135, "spreadOdds": 180.0},
}
BET365_SOCCER = {
    "provider": {"name": "Bet 365"},
    "details": None, "overUnder": None, "spread": None,
    "homeTeamOdds": {"odds": {"summary": "19/50", "value": 1.38}},
    "awayTeamOdds": {"odds": {"summary": "13/2", "value": 7.5}},
    "drawOdds": {"odds": {"value": 4.75}},
}


def by(quotes):
    return {(q.market, q.side): q for q in quotes}


def test_parse_draftkings_game_lines():
    q = by(parse_provider_odds(DK_NFL))
    assert q[("h2h", "home")].american == -470 and q[("h2h", "away")].american == 360
    assert q[("spreads", "home")].point == -8.5 and q[("spreads", "away")].point == 8.5
    assert q[("spreads", "home")].american == -115 and q[("totals", "under")].american == -105
    assert q[("totals", "over")].point == 47.5 and q[("h2h", "home")].book == "draftkings"


def test_spread_is_home_perspective():
    q = by(parse_provider_odds(DK_NHL_HOME_DOG))
    assert q[("spreads", "home")].point == 1.5 and q[("spreads", "away")].point == -1.5


def test_parse_bet365_decimal_three_way():
    q = by(parse_provider_odds(BET365_SOCCER))
    assert q[("h2h", "home")].american == -263 and q[("h2h", "away")].american == 650
    assert q[("h2h", "draw")].american == 375 and q[("h2h", "home")].book == "bet365"
    assert ("spreads", "home") not in q


def test_prop_type_names():
    assert prop_market("football", "Total Passing Yards (incl. overtime)") == "player_pass_yds"
    assert prop_market("football", "Total Receptions (incl. overtime)") == "player_receptions"
    assert prop_market("football", "Total Rushing Plus Receiving Yards (incl. overtime)") is None
    assert prop_market("football", "1st Half Total Passing Yards") is None
    assert prop_market("football", "Receiving Yards Milestones") is None
    assert prop_market("basketball", "Total Points, Rebounds and Assists") == "player_points_rebounds_assists"
    assert prop_market("basketball", "Total 3-Point Field Goals Made") == "player_threes"
    assert prop_market("basketball", "Total Points") == "player_points"
    assert prop_market("basketball", "Team Total Points") is None
    assert prop_market("hockey", "Total Shots On Goal") == "player_shots_on_goal"


class FakeEspn:
    def __init__(self, routes, games=()):
        self.routes, self.games, self.calls = routes, list(games), []

    def _get(self, url, params, ttl):
        self.calls.append((url, dict(params), ttl))
        for key, value in self.routes.items():
            if key in url:
                return value(params) if callable(value) else value
        return {}

    def game_results(self, cfg, start, end):
        return self.games


def test_prop_quotes_use_main_line_and_assumed_price():
    items = [
        {"type": {"name": "Total Receptions (incl. overtime)"}, "athlete": {"$ref": "http://x/athletes/1?lang=en"},
         "current": {"target": {"value": 3.5}}},
        {"type": {"name": "Total Receptions (incl. overtime)"}, "athlete": {"$ref": "http://x/athletes/1?lang=en"},
         "current": {"target": {"value": 4.5}}},  # alternate line, ignored
        {"type": {"name": "Receptions Milestones"}, "athlete": {"$ref": "http://x/athletes/1?lang=en"},
         "current": {"target": {"value": 2.0}}},
        {"type": {"name": "Team Total Points"}, "team": {"$ref": "t"}, "current": {"target": {"value": 20.5}}},
    ]
    espn = FakeEspn({"propBets": {"pageCount": 1, "items": items}, "athletes/1": {"displayName": "CeeDee Lamb"}})
    from sportsedge.data.base import Event
    ev = Event("401", "nfl", datetime.now(timezone.utc), "Dallas Cowboys", "Tampa Bay Buccaneers")
    quotes = EspnOdds(espn).prop_quotes(SPORTS["nfl"], ev, ["player_receptions"])
    assert [(q.player, q.side, q.point, q.american, q.book) for q in quotes] == [
        ("CeeDee Lamb", "over", 3.5, -115, ESTIMATED_BOOK), ("CeeDee Lamb", "under", 3.5, -115, ESTIMATED_BOOK)]


def test_events_and_live_events_pick_the_right_provider():
    now = datetime.now(timezone.utc)
    pre = GameResult("401", now + timedelta(hours=5), "Dallas Cowboys", "Tampa Bay Buccaneers", None, None, state="pre")
    listing = {"items": [{"$ref": "http://core/odds/100?lang=en"}, {"$ref": "http://core/odds/200?lang=en"}]}
    live_obj = dict(DK_NFL, provider={"name": "Draft Kings - Live Odds"},
                    homeTeamOdds={"moneyLine": -900, "spreadOdds": -110}, awayTeamOdds={"moneyLine": 600, "spreadOdds": -110})
    espn = FakeEspn({"/odds/100": DK_NFL, "/odds/200": live_obj, "/odds": listing}, games=[pre])
    odds = EspnOdds(espn)
    events = odds.events(SPORTS["nfl"])
    assert len(events) == 1 and by(events[0].quotes)[("h2h", "home")].american == -470
    live = odds.live_events(SPORTS["nfl"], [pre])
    assert by(live[0].quotes)[("h2h", "home")].american == -900
    assert any(ttl == 0 for url, _, ttl in espn.calls if "/odds/200" in url)  # live odds never cached


def test_mlb_inline_odds_are_read_without_following_the_link():
    now = datetime.now(timezone.utc)
    pre = GameResult("501", now + timedelta(hours=3), "Los Angeles Dodgers", "Philadelphia Phillies", None, None, state="pre")
    inline = dict(DK_NHL_HOME_DOG, **{"$ref": "http://core/odds/100?lang=en"})
    listing = {"items": [inline]}
    espn = FakeEspn({"/odds": listing}, games=[pre])  # the provider link itself would 404
    events = EspnOdds(espn).events(SPORTS["mlb"])
    assert len(events) == 1 and by(events[0].quotes)[("h2h", "home")].american == 114
    assert not any("/odds/100" in url for url, _, _ in espn.calls)


def test_games_without_a_start_time_wait():
    now = datetime.now(timezone.utc)
    tbd = GameResult("601", now + timedelta(days=2), "Milwaukee Brewers", "Los Angeles Dodgers", None, None,
                     state="pre", time_tbd=True)
    espn = FakeEspn({"/odds": {"items": [dict(DK_NFL, **{"$ref": "http://core/odds/100"})]}}, games=[tbd])
    assert EspnOdds(espn).events(SPORTS["mlb"]) == []


def test_time_valid_false_marks_tbd():
    from sportsedge.data.espn import _parse_event

    ev = {"id": "1", "date": "2026-10-11T04:00Z", "status": {"type": {"state": "pre"}},
          "competitions": [{"timeValid": False, "competitors": [
              {"homeAway": "home", "team": {"displayName": "Milwaukee Brewers"}},
              {"homeAway": "away", "team": {"displayName": "Los Angeles Dodgers"}}]}]}
    assert _parse_event(ev).time_tbd
    ev["competitions"][0]["timeValid"] = True
    assert _parse_event(ev).time_tbd  # still midnight Eastern: ESPN's TBD slot
    ev["date"] = "2026-10-11T20:08Z"
    assert not _parse_event(ev).time_tbd
