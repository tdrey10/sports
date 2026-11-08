"""Owner, Nov 8: log player props and touchdown scorer bets in The Doctor vs Grim, graded from box scores."""
from datetime import datetime, timezone
from types import SimpleNamespace

from sportsedge import rivalry
from sportsedge.data.espn import first_td_scorer, mark_first_td
from sportsedge.ledger import grade
from sportsedge.models.props import PlayerGame
from sportsedge.models.ratings import GameResult

NOW = datetime(2026, 11, 8, 23, tzinfo=timezone.utc)
GAME = GameResult("g1", NOW, "Carolina Panthers", "Denver Broncos", 17, 24)
SUMMARY = {"scoringPlays": [
    {"type": {"text": "Field Goal"}, "text": "Eddy Pineiro 40 Yd Field Goal"},
    {"type": {"text": "Passing Touchdown"}, "text": "Jalen Coker 12 Yd pass from Bryce Young (Eddy Pineiro Kick)"},
    {"type": {"text": "Rushing Touchdown"}, "text": "J.K. Dobbins 3 Yd Run (Wil Lutz Kick)"}]}


def _entry(market, player, point=0.5, side="over", odds=1100.0):
    return SimpleNamespace(home=GAME.home, away=GAME.away, sport="nfl", market=market, side=side, point=point, odds=odds,
                           units=0.0, player=player, reasons=[], final=None, live=None, graded_at=None, status="pending",
                           profit=None, result_value=None)


def _box():
    box = [PlayerGame("g1", NOW, "Jalen Coker", "Carolina Panthers", {"receiving.REC": 4, "receiving.TD": 1}),
           PlayerGame("g1", NOW, "J.K. Dobbins", "Denver Broncos", {"rushing.YDS": 80, "rushing.TD": 1}),
           PlayerGame("g1", NOW, "Bryce Young", "Carolina Panthers", {"passing.YDS": 250, "passing.TD": 1, "rushing.TD": 0})]
    mark_first_td(box, SUMMARY, "g1", NOW)
    return box


def test_first_td_scorer_is_read_from_the_scoring_plays():
    assert first_td_scorer(SUMMARY) == "Jalen Coker"


def test_first_and_anytime_td_grade_off_the_box_score():
    e = _entry("player_first_td", "Jalen Coker")
    grade(e, GAME, _box(), NOW)
    assert e.status == "won" and e.profit == 11.0
    e = _entry("player_first_td", "J.K. Dobbins")
    grade(e, GAME, _box(), NOW)
    assert e.status == "lost"
    e = _entry("player_anytime_td", "J.K. Dobbins", odds=-120)
    grade(e, GAME, _box(), NOW)
    assert e.status == "won"
    e = _entry("player_anytime_td", "Bryce Young", odds=400)  # a passing TD isn't a scoring TD for him
    grade(e, GAME, _box(), NOW)
    assert e.status == "lost"


def test_parse_leg_reads_props_and_td_bets():
    assert rivalry.parse_leg("nfl|Carolina Panthers|1100|player Jalen Coker first_td") == \
        ("nfl", "Carolina Panthers", 1100.0, "player_first_td@Jalen Coker", 0.5, "over")
    assert rivalry.parse_leg("nba|Los Angeles Lakers|-115|player LeBron James pra over 40.5")[3:] == \
        ("player_points_rebounds_assists@LeBron James", 40.5, "over")
