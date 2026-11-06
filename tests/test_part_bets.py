"""Owner, Nov 5: bets on part of a game (2nd half over 108.5, 1st half spreads, quarters, hockey periods)."""
from datetime import datetime, timezone
from types import SimpleNamespace

from sportsedge import rivalry
from sportsedge.ledger import grade, part_scores, split_market
from sportsedge.models.ratings import GameResult

NOW = datetime(2026, 11, 6, 6, tzinfo=timezone.utc)


def _game(hp, ap):
    return GameResult("g1", NOW, "Phoenix Suns", "Orlando Magic", sum(hp), sum(ap), home_periods=tuple(hp), away_periods=tuple(ap))


def _entry(market, side, point, odds=-110.0):
    return SimpleNamespace(home="Phoenix Suns", away="Orlando Magic", sport="nba", market=market, side=side, point=point,
                           odds=odds, units=0.0, player=None, reasons=[], final=None, live=None, graded_at=None,
                           status="pending", profit=None, result_value=None)


def test_split_and_part_scores():
    assert split_market("totals_2h") == ("totals", "2h") and split_market("totals") == ("totals", None)
    g = _game([30, 25, 28, 27, 10], [20, 30, 26, 24, 8])  # an overtime period at the end
    assert part_scores(g, "1h", "nba") == (55, 50)
    assert part_scores(g, "2h", "nba") == (65, 58)  # the second half takes in overtime
    assert part_scores(g, "q4", "nba") == (27, 24)  # a quarter doesn't
    cbb = GameResult("g2", NOW, "A", "B", 70, 60, home_periods=(30.0, 40.0), away_periods=(35.0, 25.0))
    assert part_scores(cbb, "2h", "ncaab") == (40, 25)
    assert part_scores(cbb, "q1", "ncaab") is None


def test_second_half_over_is_graded_on_the_second_half_only():
    g = _game([30, 25, 28, 27], [20, 30, 26, 24])  # 2nd half 55 + 50 = 105, full game 210
    e = _entry("totals_2h", "over", 108.5)
    grade(e, g, None, NOW)
    assert e.status == "lost" and e.result_value == 105
    assert any(r.startswith("2nd half:") for r in e.reasons)
    e = _entry("totals_2h", "under", 108.5)
    grade(e, g, None, NOW)
    assert e.status == "won"


def test_no_period_scores_voids_instead_of_guessing():
    g = GameResult("g1", NOW, "Phoenix Suns", "Orlando Magic", 110, 100)
    e = _entry("spreads_1h", "home", -2.5)
    grade(e, g, None, NOW)
    assert e.status == "void"


def test_parse_leg_takes_a_part():
    assert rivalry.parse_leg("nba|Orlando Magic|-110|over 108.5|2h") == ("nba", "Orlando Magic", -110.0, "totals_2h", 108.5, "over")
    assert rivalry.parse_leg("nba|Lakers|-115|ml|1h")[3] == "h2h_1h"
    assert rivalry.parse_leg("nfl|Bills|-110|spread -3.5")[3] == "spreads"
