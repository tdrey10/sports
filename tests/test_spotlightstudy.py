from datetime import datetime, timezone

from sportsedge import spotlightstudy as S
from tests.test_dogstudy import _g


def _at(sport, utc, home="A", away="B", season=2022):
    g = _g(0, home, away, 1, 0)
    g.sport, g.start, g.season = sport, utc, season
    return g


def test_nba_late_tip_then_next_day_is_the_spot():
    prev = _at("nba", datetime(2023, 1, 2, 3, 30, tzinfo=timezone.utc))  # 10:30 PM ET Jan 1
    g = _at("nba", datetime(2023, 1, 3, 0, 0, tzinfo=timezone.utc))  # 7 PM ET next day
    assert S.in_spot("nba", prev, g)
    early = _at("nba", datetime(2023, 1, 2, 0, 0, tzinfo=timezone.utc))  # 7 PM ET tip: no
    assert not S.in_spot("nba", early, g)
    later = _at("nba", datetime(2023, 1, 6, 0, 0, tzinfo=timezone.utc))  # 4 days later: no
    assert not S.in_spot("nba", prev, later)


def test_previous_game_must_be_same_season_and_earlier():
    prev = _at("nfl", datetime(2023, 1, 2, 1, 15, tzinfo=timezone.utc), season=2021)  # 8:15 PM ET
    g = _at("nfl", datetime(2023, 1, 8, 18, 0, tzinfo=timezone.utc))
    assert not S.in_spot("nfl", prev, g)
    prev.season = 2022
    assert S.in_spot("nfl", prev, g)
    assert not S.in_spot("nfl", g, prev)  # a later game never counts
    assert not S.in_spot("nfl", None, g)


def test_mlb_day_game_after_night_game():
    prev = _at("mlb", datetime(2023, 6, 2, 23, 10, tzinfo=timezone.utc))  # 7:10 PM ET
    day = _at("mlb", datetime(2023, 6, 3, 17, 5, tzinfo=timezone.utc))  # 1:05 PM ET next day
    night = _at("mlb", datetime(2023, 6, 3, 23, 5, tzinfo=timezone.utc))
    assert S.in_spot("mlb", prev, day) and not S.in_spot("mlb", prev, night)


def test_spots_tag_side_opponent_and_totals():
    prev = _at("nba", datetime(2023, 1, 2, 3, 30, tzinfo=timezone.utc), home="A", away="C")
    g = _at("nba", datetime(2023, 1, 3, 0, 0, tzinfo=timezone.utc))  # A home -150, B away +130
    spots, every = S.spots_for("nba", g, {"home": prev, "away": None})
    names = {s[0] for s in spots}
    assert "lock-price fav ML: off a late-night game" in names
    assert "dog ML: vs a team off a late-night game" in names
    assert {"over: a team off a late-night game", "under: a team off a late-night game"} <= names
    assert len(every) == 4  # 2 ML + 2 totals (no spread in this game)


def test_study_splits_tuning_and_blind_and_runs_control():
    games = []
    for season, yr in ((2019, 2019), (2023, 2023)):
        games.append(_at("nba", datetime(yr, 1, 2, 3, 30, tzinfo=timezone.utc), season=season))
        games.append(_at("nba", datetime(yr, 1, 3, 0, 0, tzinfo=timezone.utc), season=season))
    recs, ctl = {}, {}
    S.study(games, "nba", recs, ctl, n_random=5)
    r = recs[("nba", "lock-price fav ML: off a late-night game")]
    assert r["in_sample"].bets == 1 and r["blind"].bets == 1
