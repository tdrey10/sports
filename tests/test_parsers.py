from datetime import datetime, timezone

import pytest

from sportsedge.data.cache import JsonCache
from sportsedge.data.espn import EspnClient, innings_to_outs, parse_box_score
from sportsedge.data.oddsapi import OddsApiClient, parse_event
from sportsedge.models.props import stat_value
from sportsedge.sports import SPORTS

WHEN = datetime(2026, 1, 1, tzinfo=timezone.utc)


def espn_event(eid, home, away, hs, as_, completed=True, date="2025-12-01T00:30Z"):
    return {
        "id": eid,
        "date": date,
        "status": {"type": {"completed": completed}},
        "competitions": [{
            "neutralSite": False,
            "competitors": [
                {"homeAway": "home", "score": str(hs), "team": {"displayName": home}},
                {"homeAway": "away", "score": str(as_), "team": {"displayName": away}},
            ],
        }],
    }


NBA_SUMMARY = {
    "boxscore": {"players": [{
        "team": {"displayName": "Boston Celtics"},
        "statistics": [{
            "labels": ["MIN", "FG", "3PT", "REB", "AST", "PTS"],
            "keys": ["minutes", "fieldGoalsMade-fieldGoalsAttempted", "threePointFieldGoalsMade-threePointFieldGoalsAttempted",
                     "rebounds", "assists", "points"],
            "athletes": [
                {"athlete": {"displayName": "Jayson Tatum"}, "stats": ["38", "10-21", "4-9", "8", "6", "29"]},
                {"athlete": {"displayName": "Bench Guy"}, "didNotPlay": True, "stats": []},
            ],
        }],
    }]}
}

NFL_SUMMARY = {
    "boxscore": {"players": [{
        "team": {"displayName": "Kansas City Chiefs"},
        "statistics": [
            {"name": "passing", "labels": ["C/ATT", "YDS", "TD", "INT"],
             "athletes": [{"athlete": {"displayName": "Patrick Mahomes"}, "stats": ["24/35", "281", "2", "1"]}]},
            {"name": "rushing", "labels": ["CAR", "YDS", "TD"],
             "athletes": [{"athlete": {"displayName": "Patrick Mahomes"}, "stats": ["5", "33", "0"]}]},
        ],
    }]}
}

MLB_SUMMARY = {
    "boxscore": {"players": [{
        "team": {"displayName": "New York Yankees"},
        "statistics": [
            {"type": "pitching", "labels": ["IP", "H", "ER", "BB", "K"],
             "athletes": [{"athlete": {"displayName": "Gerrit Cole"}, "stats": ["6.2", "5", "2", "1", "9"]}]},
        ],
    }]}
}


def test_parse_nba_box():
    rows = parse_box_score(NBA_SUMMARY, "1", WHEN)
    assert [r.player for r in rows] == ["Jayson Tatum"]
    s = rows[0].stats
    assert s["PTS"] == 29 and s["3PT"] == 4 and s["3PT_A"] == 9
    spec = SPORTS["nba"].props["player_points_rebounds_assists"]
    assert stat_value(spec, s) == 43


def test_parse_nfl_grouped_box():
    s = parse_box_score(NFL_SUMMARY, "1", WHEN)[0].stats
    assert s["passing.YDS"] == 281 and s["rushing.YDS"] == 33
    assert s["passing.C/ATT"] == 24 and s["passing.C/ATT_A"] == 35
    assert s["YDS"] == 281  # bare label keeps the first group's value
    props = SPORTS["nfl"].props
    assert stat_value(props["player_pass_attempts"], s) == 35
    assert stat_value(props["player_rush_yds"], s) == 33


def test_parse_mlb_innings():
    s = parse_box_score(MLB_SUMMARY, "1", WHEN)[0].stats
    assert s["pitching.IP_OUTS"] == 20
    assert stat_value(SPORTS["mlb"].props["pitcher_strikeouts"], s) == 9
    assert innings_to_outs(5.1) == 16


class FakeResponse:
    def __init__(self, payload, headers=None):
        self.payload, self.headers = payload, headers or {}

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, router):
        self.router, self.calls, self.headers = router, [], {}

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params))
        return FakeResponse(self.router(url, params or {}), {"x-requests-remaining": "499"})


def test_espn_client_results_and_box_scores(tmp_path):
    def router(url, params):
        if url.endswith("/scoreboard"):
            return {"events": [
                espn_event("10", "Boston Celtics", "Miami Heat", 110, 101),
                espn_event("11", "Miami Heat", "Boston Celtics", 0, 0, completed=False, date="2026-01-01T20:30Z"),
            ]}
        return NBA_SUMMARY

    session = FakeSession(router)
    client = EspnClient(cache=JsonCache(tmp_path), session=session)
    cfg = SPORTS["nba"]
    results = client.game_results(cfg, datetime(2025, 11, 25, tzinfo=timezone.utc), WHEN)
    assert [(g.event_id, g.completed) for g in results] == [("10", True), ("11", False)]
    assert results[0].home_score == 110
    logs = client.player_games(cfg, results, "Boston Celtics")
    assert logs and logs[0].stats["PTS"] == 29
    n_calls = len(session.calls)
    client.player_games(cfg, results, "Boston Celtics")
    assert len(session.calls) == n_calls  # completed box scores come from the cache


ODDS_EVENT = {
    "id": "abc",
    "commence_time": "2026-01-02T00:00:00Z",
    "home_team": "Arsenal",
    "away_team": "Chelsea",
    "bookmakers": [{
        "key": "draftkings",
        "markets": [
            {"key": "h2h", "outcomes": [
                {"name": "Arsenal", "price": -120}, {"name": "Chelsea", "price": 320}, {"name": "Draw", "price": 270}]},
            {"key": "spreads", "outcomes": [
                {"name": "Arsenal", "price": -105, "point": -0.75}, {"name": "Chelsea", "price": -115, "point": 0.75}]},
            {"key": "totals", "outcomes": [
                {"name": "Over", "price": -110, "point": 2.5}, {"name": "Under", "price": -110, "point": 2.5}]},
            {"key": "player_shots_on_target", "outcomes": [
                {"name": "Over", "description": "Bukayo Saka", "price": 120, "point": 1.5},
                {"name": "Yes", "description": "Bukayo Saka", "price": 200}]},
        ],
    }],
}


def test_parse_odds_event():
    ev = parse_event(SPORTS["epl"], ODDS_EVENT)
    sides = {(q.market, q.side) for q in ev.quotes}
    assert {("h2h", "home"), ("h2h", "away"), ("h2h", "draw"), ("spreads", "home"), ("totals", "over")} <= sides
    props = [q for q in ev.quotes if q.player]
    assert len(props) == 1 and props[0].side == "over" and props[0].point == 1.5


def test_odds_client_requires_key(monkeypatch):
    monkeypatch.delenv("ODDS_API_KEY", raising=False)
    with pytest.raises(RuntimeError):
        OddsApiClient()


def test_odds_client_request(monkeypatch):
    session = FakeSession(lambda url, params: [ODDS_EVENT])
    client = OddsApiClient(api_key="k", session=session, bookmakers=["draftkings"])
    events = client.events(SPORTS["epl"])
    url, params = session.calls[0]
    assert url.endswith("/sports/soccer_epl/odds")
    assert params["bookmakers"] == "draftkings" and "regions" not in params
    assert events[0].home == "Arsenal" and client.requests_remaining == "499"


def test_espn_never_requests_date_ranges(tmp_path):
    session = FakeSession(lambda url, params: {"events": []})
    client = EspnClient(cache=JsonCache(tmp_path), session=session)
    client.game_results(SPORTS["nba"], datetime(2026, 1, 15, tzinfo=timezone.utc), datetime(2026, 3, 2, tzinfo=timezone.utc))
    dates = [p["dates"] for _, p in session.calls]
    assert dates == ["202601", "202602", "202603"]  # pro leagues: one request per month
    assert all(p["limit"] == 1000 for _, p in session.calls)

    session.calls.clear()
    client.game_results(SPORTS["ncaab"], datetime(2026, 2, 27, tzinfo=timezone.utc), datetime(2026, 3, 2, tzinfo=timezone.utc))
    dates = [p["dates"] for _, p in session.calls]
    assert dates == ["20260227", "20260228", "20260301", "20260302"]  # college: day by day
    assert all("-" not in d for d in dates)


def test_espn_month_falls_back_to_days_when_truncated(tmp_path):
    def router(url, params):
        if len(params["dates"]) == 6:
            return {"events": [espn_event(str(i), "A", "B", 1, 0) for i in range(1000)]}
        return {"events": []}
    session = FakeSession(router)
    client = EspnClient(cache=JsonCache(tmp_path), session=session)
    client.game_results(SPORTS["mlb"], datetime(2026, 8, 1, tzinfo=timezone.utc), datetime(2026, 8, 31, tzinfo=timezone.utc))
    assert len([1 for _, p in session.calls if len(p["dates"]) == 8]) == 31
