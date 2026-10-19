from sportsedge import movestudy as M
from tests.test_dogstudy import _g


def _with(g, open_home, open_away, home, away):
    g.open_ml = {"home": open_home, "away": open_away}
    g.ml = {"home": home, "away": away}
    return g


def test_implied_handles_both_signs():
    assert M.implied(100) == 0.5
    assert abs(M.implied(-200) - 2 / 3) < 1e-9
    assert M.implied(None) is None


def test_move_is_close_minus_open():
    g = _with(_g(0, "A", "B", 1, 0), -150, 130, -170, 150)
    assert M.move(g, "home") > 0 > M.move(g, "away")


def test_dog_that_shortened_gets_toward_spots_only():
    g = _with(_g(0, "A", "B", 1, 0), -200, 170, -150, 130)
    assert "dog moved toward by 3%+" in M.spots(g, "away")
    assert not any("drifted" in s for s in M.spots(g, "away"))
    assert M.spots(g, "home") == []  # favorite lost ground: not a spot


def test_no_opening_line_no_spot():
    g = _g(0, "A", "B", 1, 0)
    g.open_ml = {"home": None, "away": None}
    assert M.spots(g, "away") == []


def test_run_with_no_data_is_empty(tmp_path):
    assert M.run(tmp_path)["rows"] == []
