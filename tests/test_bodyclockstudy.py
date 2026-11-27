from datetime import date, datetime, timezone

from sportsedge import bodyclockstudy as B
from tests.test_dogstudy import _g


def _at(utc, tzo, home="SEA", away="NYJ", neutral=False):
    g = _g(0, home, away, 1, 0)
    g.sport, g.start, g.tzo, g.neutral = "nfl", utc, tzo, neutral
    return g


def test_us_daylight_time_dates():
    assert B.us_dst(date(2024, 3, 10)) and not B.us_dst(date(2024, 3, 9))  # 2nd Sunday of March
    assert B.us_dst(date(2024, 11, 2)) and not B.us_dst(date(2024, 11, 3))  # 1st Sunday of November
    assert B.standard(-4.0, date(2024, 10, 6)) == -5.0 and B.standard(-5.0, date(2024, 12, 1)) == -5.0


def test_west_coast_team_in_a_1pm_eastern_game_plays_at_10am_its_time():
    g = _at(datetime(2024, 10, 6, 17, 0, tzinfo=timezone.utc), -4.0, home="NYJ", away="SEA")  # 1pm EDT
    c = B.clock(g, -8.0)  # Seattle, standard time
    assert c["gap"] == -3 and c["venue_h"] == 13 and c["body_h"] == 10
    assert B.spots_in(c) == ["west visitor, body clock before 11am", "any visitor, body clock before noon"]
    night = _at(datetime(2024, 10, 7, 0, 20, tzinfo=timezone.utc), -4.0)  # 8:20pm EDT
    assert B.spots_in(B.clock(night, -8.0)) == ["west visitor, night game"]


def test_east_team_out_west_late_and_same_zone_games_are_no_spot():
    late = _at(datetime(2024, 1, 10, 3, 30, tzinfo=timezone.utc), -8.0)  # 7:30pm PST = 10:30pm EST
    assert B.spots_in(B.clock(late, -5.0)) == ["east visitor, body clock 10pm+"]
    home_team_zone = _at(datetime(2024, 10, 6, 17, 0, tzinfo=timezone.utc), -4.0)
    assert B.spots_in(B.clock(home_team_zone, -5.0)) == []  # same zone: no shock even at 1pm
    assert B.clock(home_team_zone, None) is None and B.spots_in(None) == []


def test_spots_tag_visitor_home_and_totals():
    g = _at(datetime(2024, 10, 6, 17, 0, tzinfo=timezone.utc), -4.0)  # home -150, away +130, total 200
    spots, every = B.spots_for(g, -8.0)
    names = {s[0] for s in spots}
    assert "dog ML: west visitor, body clock before 11am" in names
    assert "lock-price fav ML: home vs a west visitor, body clock before 11am" in names
    assert "under: west visitor, body clock before 11am" in names
    assert len(every) == 4


def test_home_zone_comes_from_earlier_home_games_only():
    games = []
    for i in range(3):  # SEA hosts three games in Pacific time, then visits New York
        h = _at(datetime(2024, 9, 1 + i, 20, 0, tzinfo=timezone.utc), -7.0, home="SEA", away=f"X{i}")
        h.gid = str(i)
        games.append(h)
    trip = _at(datetime(2024, 10, 6, 17, 0, tzinfo=timezone.utc), -4.0, home="NYJ", away="SEA")
    trip.gid = "9"
    recs, control = {}, {}
    B.study(games + [trip], "nfl", recs, control, n_random=0)
    assert ("nfl", "dog ML: west visitor, body clock before 11am") in recs
    recs2 = {}
    B.study(games[:2] + [trip], "nfl", recs2, {}, n_random=0)  # only two earlier home games: no zone yet
    assert recs2 == {}
