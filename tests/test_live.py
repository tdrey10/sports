from datetime import datetime, timedelta, timezone

import pytest

from sportsedge.data.base import Event, NameMatcher, Quote
from sportsedge.edge import Settings
from sportsedge.ledger import Ledger
from sportsedge.live import LiveScanner, fraction_remaining, live_cards, live_prediction
from sportsedge.models.game import predict_game
from sportsedge.models.ratings import GameResult, TeamRatings
from sportsedge.notify import GitHubIssueNotifier, format_live_card
from sportsedge.sports import SPORTS

NOW = datetime(2026, 10, 11, 20, tzinfo=timezone.utc)


def live_game(sport, **kw):
    base = dict(event_id="espn1", date=NOW - timedelta(hours=2), home="Home Team", away="Away Team",
                home_score=None, away_score=None, state="in")
    base.update(kw)
    return GameResult(**base)


@pytest.mark.parametrize("sport,kw,expected", [
    ("nfl", dict(period=3, clock="15:00"), 0.5),
    ("nfl", dict(period=4, clock="7:30"), 0.125),
    ("nba", dict(period=2, clock="6:00"), 0.625),
    ("ncaab", dict(period=2, clock="10:00"), 0.25),
    ("nhl", dict(period=1, clock="20:00"), 1.0),
    ("mlb", dict(period=7, detail="Bot 7th"), 5 / 18),
    ("epl", dict(period=2, clock="60'"), 1 / 3),
])
def test_fraction_remaining(sport, kw, expected):
    assert fraction_remaining(SPORTS[sport], live_game(sport, **kw)) == pytest.approx(expected, abs=1e-3)


def test_fraction_unknown_when_not_live():
    assert fraction_remaining(SPORTS["nfl"], live_game("nfl", state="pre", period=0)) is None


def test_live_prediction_leader_late_is_big_favourite():
    cfg = SPORTS["nba"]
    r = TeamRatings("identity", 112, 1.5, {"Home Team": 0, "Away Team": 0}, {"Home Team": 0, "Away Team": 0})
    pre = predict_game(cfg, r, "Home Team", "Away Team")
    early = live_prediction(cfg, pre, live_game("nba", period=1, clock="11:00", live_home=4, live_away=0), 0.98)
    late = live_prediction(cfg, pre, live_game("nba", period=4, clock="2:00", live_home=104, live_away=96), 2 / 48)
    assert late.p_home > 0.9 > early.p_home > pre.p_home - 0.05
    assert late.exp_total == pytest.approx(200 + 224 * 2 / 48, abs=0.5)


class FakeLiveProvider:
    def __init__(self, game, event):
        self.game, self.event = game, event

    def live_games(self, cfg):
        return [self.game]

    def events(self, cfg):
        return [self.event]

    def game_results(self, cfg, start, end):
        out = []
        for i in range(120):
            h, a = ("Home Team", "Away Team") if i % 2 else ("Away Team", "Home Team")
            out.append(GameResult(f"g{i}", NOW - timedelta(days=100 - i / 2), h, a, 24 + (i % 7), 20 + (i % 5)))
        return out


def make_event(home_ml, away_ml, over, under, total=44.5):
    ev = Event("odds1", "nfl", NOW - timedelta(hours=2), "Home Team", "Away Team")
    for book in ("draftkings", "fanduel", "pinnacle"):
        ev.quotes += [
            Quote("h2h", "home", None, book, home_ml), Quote("h2h", "away", None, book, away_ml),
            Quote("totals", "over", total, book, over), Quote("totals", "under", total, book, under),
        ]
    return ev


def test_live_scanner_prices_in_play_games_and_publishes_once():
    game = live_game("nfl", period=4, clock="10:00", live_home=27, live_away=10, detail="10:00 - 4th")
    # Books still pricing it close to a coin flip: a clear live edge on the home side.
    scanner = LiveScanner(FakeLiveProvider(game, make_event(-150, 125, -110, -110)), Settings())
    picks = scanner.scan(SPORTS["nfl"], NOW)
    assert picks and all("(live)" in p.selection for p in picks)
    cards = live_cards(picks, scanner, {"nfl": "NFL"})
    L = Ledger()
    added = L.publish(cards, NOW)
    assert len(added) <= 1 and all(e.tier == "live" for e in added)
    assert L.publish(live_cards(scanner.scan(SPORTS["nfl"], NOW), scanner, {"nfl": "NFL"}), NOW) == []


def test_live_scanner_skips_garbage_time_and_finished_games():
    late = live_game("nfl", period=4, clock="1:00", live_home=27, live_away=10)
    assert LiveScanner(FakeLiveProvider(late, make_event(-150, 125, -110, -110))).scan(SPORTS["nfl"], NOW) == []
    final = live_game("nfl", state="post", period=4, clock="0:00")
    assert LiveScanner(FakeLiveProvider(final, make_event(-150, 125, -110, -110))).scan(SPORTS["nfl"], NOW) == []


def test_live_bet_does_not_block_pregame_and_vice_versa():
    from tests.test_ledger import card, make_pick
    L = Ledger()
    pre = make_pick(event_id="odds1")
    assert len(L.publish([card(pre)], NOW)) == 1
    live = card(make_pick(event_id="odds1"), tier="live")
    assert len(L.publish([live], NOW)) == 1


class FakeGitHub:
    def __init__(self):
        self.headers, self.posts = {}, []

    def get(self, url, params=None):
        return Resp([{"number": 7, "title": "Funky Buttlovin live bets"}])

    def post(self, url, json=None):
        self.posts.append((url, json))
        return Resp({"number": 7})


class Resp:
    def __init__(self, data):
        self.data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self.data


def test_notifier_posts_to_existing_issue():
    from tests.test_ledger import card, make_pick
    gh = FakeGitHub()
    n = GitHubIssueNotifier(repo="me/sports", token="t", session=gh)
    c = card(make_pick(), tier="live")
    n.post(format_live_card(c))
    url, body = gh.posts[0]
    assert url.endswith("/repos/me/sports/issues/7/comments")
    assert "EV" in body["body"] and "unit" in body["body"]


def test_team_aliases():
    m = NameMatcher(["Athletic Club", "Internazionale", "Red Bull New York", "LAFC", "Massachusetts Minutemen"])
    assert m.match("Athletic Bilbao") == "Athletic Club"
    assert m.match("Inter Milan") == "Internazionale"
    assert m.match("New York Red Bulls") == "Red Bull New York"
    assert m.match("Los Angeles FC") == "LAFC"
    assert m.match("UMass Minutemen") == "Massachusetts Minutemen"
