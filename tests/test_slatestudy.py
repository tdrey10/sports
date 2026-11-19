"""Slate size study (Nov 19): busy vs quiet days, cutoffs from tuning seasons only."""

from datetime import timedelta

from sportsedge import slatestudy as S
from tests.test_dogstudy import _g


def test_slate_counts_games_on_the_same_eastern_day():
    a, b, c = _g(0, "A", "B", 1, 0), _g(0, "C", "D", 1, 0), _g(0, "E", "F", 1, 0)
    c.start = a.start + timedelta(hours=6)  # 00:00 UTC is 7 PM the day before in the East; 06:00 UTC is 1 AM, a new day
    assert S.slates([a, b, c]) == [2, 2, 1]


def test_spots_and_no_split_without_spread_in_sizes():
    assert S.spot(12, (3, 10)) == "busy day" and S.spot(2, (3, 10)) == "quiet day" and S.spot(5, (3, 10)) is None
    games = [_g(i, "A", "B", 1, 0) for i in range(100)]  # one game a day, every day
    assert S.cutoffs(games, S.slates(games)) is None


def test_bets_grade_moneylines_and_totals():
    g = _g(0, "A", "B", 110, 100)  # home -150 wins, total 200 -> over
    kinds = {k: r for k, _, r in S.bets(g)}
    assert kinds == {"lock-price fav ML": 1.0, "dog ML": 0.0, "over": 1.0, "under": 0.0}


def test_run_with_no_data_is_empty(tmp_path):
    assert S.run(tmp_path)["rows"] == []
