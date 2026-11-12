"""Owner, Nov 12 ("do both"): a Lock and a Dog every day, no money unless proven, with their own record."""

from datetime import timedelta

from sportsedge.ledger import Ledger
from sportsedge.runner import daily_lock_dog
from tests.test_ledger import START, card, make_pick

NOW = START - timedelta(hours=3)


def _lean(event_id, american, win_prob, market_prob, ev):
    return card(make_pick(market="h2h", point=None, american=american, event_id=event_id, win_prob=win_prob,
                          market_prob=market_prob, ev=ev), tier="lean", units=0.0)


def test_best_lean_favorite_and_dog_take_the_slots_at_no_money():
    cards = [_lean("a", -130, 0.58, 0.55, 0.03), _lean("b", -140, 0.62, 0.57, 0.06), _lean("c", -200, 0.70, 0.65, 0.05),
             _lean("d", 150, 0.42, 0.38, 0.05), _lean("e", 130, 0.40, 0.42, -0.08)]
    picks = {c.tier: c for c in daily_lock_dog(cards, Ledger(), NOW)}
    assert picks["lock"].pick.event_id == "b" and picks["lock"].units == 0  # -200 is past the -150 cap
    assert picks["dog"].pick.event_id == "d"  # "e" doesn't beat its price
    assert "no money" in picks["lock"].headline


def test_a_day_with_a_lock_already_gets_no_second_one():
    led = Ledger()
    first = daily_lock_dog([_lean("b", -140, 0.62, 0.57, 0.06)], led, NOW)
    led.publish(first, NOW)
    assert daily_lock_dog([_lean("a", -130, 0.60, 0.55, 0.05)], led, NOW) == []
