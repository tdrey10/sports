from datetime import datetime, timezone

from sportsedge import slotstudy as S
from tests.test_dogstudy import _g


def _at(sport, utc):
    g = _g(0, "A", "B", 1, 0)
    g.sport = sport
    g.start = utc
    g.ml = {"home": -140, "away": 120}
    return g


def test_slots_use_eastern_time():
    # 00:15 UTC Tuesday is Monday 8:15 PM Eastern (Monday night football)
    g = _at("nfl", datetime(2026, 10, 20, 0, 15, tzinfo=timezone.utc))
    assert "Mon night" in S.slots(g)


def test_nfl_sunday_windows():
    g = _at("nfl", datetime(2026, 10, 18, 17, 0, tzinfo=timezone.utc))  # 1 PM ET
    assert "Sun early" in S.slots(g)
    g = _at("nfl", datetime(2026, 10, 19, 0, 20, tzinfo=timezone.utc))  # 8:20 PM ET Sunday
    assert "Sun night" in S.slots(g)


def test_spots_label_dog_and_lock_only():
    g = _at("nba", datetime(2026, 10, 21, 23, 0, tzinfo=timezone.utc))
    assert all(s.startswith("lock favorite") for s in S.spots(g, "home"))
    assert all(s.startswith("dog") for s in S.spots(g, "away"))
    g.ml = {"home": -400, "away": 320}
    assert S.spots(g, "home") == [] and S.spots(g, "away") == []


def test_run_with_no_data_is_empty(tmp_path):
    assert S.run(tmp_path)["rows"] == []
