"""Owner, Oct 31: test the NFL key numbers."""

from sportsedge import keystudy


def test_a_spread_gets_its_key_number_spots():
    v = dict((name, side) for side, name in keystudy.variants(-3.5))
    assert v["favorite -3.5 (laying the hook past 3)"] == "home" and v["dog +3.5 (hook past 3)"] == "away"
    assert any(n.startswith("dog +3.5 (hook past 3) · road") for n in v)
    assert keystudy.variants(-5.0) == []  # not near 3, 7 or 10
    on = dict((name, side) for side, name in keystudy.variants(7.0))
    assert on["favorite -7 (on the number)"] == "away"
