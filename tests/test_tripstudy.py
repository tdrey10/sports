from sportsedge import tripstudy as T
from tests.test_dogstudy import _g


def test_runs_count_only_earlier_games():
    games = [_g(0, "A", "B", 1, 0), _g(1, "C", "B", 1, 0), _g(2, "D", "B", 1, 0), _g(3, "B", "A", 1, 0)]
    r = T.venue_runs(games)
    assert r[0]["away"] == {"kind": "road", "run": 0, "prev": None}
    assert r[2]["away"]["run"] == 2  # B has played two road games before game 2
    assert r[3]["home"] == {"kind": "home", "run": 0, "prev": "road"}  # first home game after the trip


def test_new_season_resets_and_neutral_breaks_runs():
    games = [_g(0, "A", "B", 1, 0), _g(1, "C", "B", 1, 0)]
    games[1].season = 2023
    assert T.venue_runs(games)[1]["away"]["run"] == 0
    games = [_g(0, "A", "B", 1, 0), _g(1, "C", "B", 1, 0)]
    games[0].neutral = True
    assert T.venue_runs(games)[1]["away"] == {"kind": "road", "run": 0, "prev": "neutral"}


def test_spots_label_trip_for_dog_only_in_price_band():
    games = [_g(i, f"H{i}", "B", 1, 0) for i in range(4)]
    for g in games:
        g.ml = {"home": -140, "away": 120}
    runs = T.venue_runs(games)
    assert "dog: road trip, 3+ road games already" in T.spots(games[3], "away", "nba", runs[3])
    assert "lock favorite: facing a team on a 3+ game road trip" in T.spots(games[3], "home", "nba", runs[3])
    games[3].ml = {"home": -400, "away": 320}
    assert T.spots(games[3], "away", "nba", runs[3]) == []


def test_run_with_no_data_is_empty(tmp_path):
    assert T.run(tmp_path)["rows"] == []
