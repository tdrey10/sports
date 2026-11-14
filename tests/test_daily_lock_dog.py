"""Owner, Nov 12 ("do both"): a Lock and a Dog every day, no money unless proven, with their own record."""

from datetime import timedelta

from sportsedge.ledger import Ledger
from sportsedge.runner import daily_lock_dog
from tests.test_ledger import START, card, make_pick

NOW = START - timedelta(hours=3)


def _lean(event_id, american, win_prob, market_prob, ev, sport="nba"):
    # NBA by default: in football a favorite only our model likes (make_pick's model 0.60, 2+ points over the
    # market) can never be the Lock (tiers.favorite_overreach), tested on its own below.
    return card(make_pick(market="h2h", point=None, american=american, event_id=event_id, win_prob=win_prob,
                          market_prob=market_prob, ev=ev, sport=sport), tier="lean", units=0.0)


def _ledger(*cards):
    led = Ledger()
    led.publish(list(cards), NOW - timedelta(hours=6))  # posted in the morning, like the real board
    return led


def test_best_posted_lean_favorite_and_dog_take_the_slots_at_no_money():
    led = _ledger(_lean("a", -130, 0.58, 0.55, 0.03), _lean("b", -140, 0.62, 0.57, 0.06), _lean("c", -200, 0.70, 0.65, 0.05),
                  _lean("d", 150, 0.42, 0.38, 0.05), _lean("e", 130, 0.40, 0.42, -0.08))
    picks = {e.tier: e for e in daily_lock_dog(led, NOW)}
    assert picks["lock"].event_id == "b" and picks["lock"].units == 0  # -200 is past the -150 cap
    assert picks["dog"].event_id == "d"  # "e" doesn't beat its price
    assert "no money" in picks["lock"].headline
    assert {"lock_lean", "dog_lean"} <= led.taken_today(NOW)


def test_a_day_with_a_lock_already_gets_no_second_one():
    led = _ledger(_lean("b", -140, 0.62, 0.57, 0.06), _lean("a", -130, 0.60, 0.55, 0.05))
    assert [e.tier for e in daily_lock_dog(led, NOW)] == ["lock"]
    assert daily_lock_dog(led, NOW) == []


def test_the_daily_dog_needs_a_real_shot_and_the_lock_skips_model_only_football_favorites():
    """Bug fix, Nov 12: the promoter skipped two standing rules. A dog under 40% was promoted, then pulled by
    withdraw (voiding the lean with it), and a football favorite only our model liked could become the Lock."""
    led = _ledger(_lean("long", 280, 0.33, 0.25, 0.25), _lean("real", 140, 0.45, 0.42, 0.08),
                  _lean("model", -140, 0.66, 0.55, 0.10, sport="nfl"), _lean("fair", -130, 0.58, 0.59, 0.02, sport="nfl"))
    picks = {e.tier: e.event_id for e in daily_lock_dog(led, NOW)}
    assert picks == {"dog": "real", "lock": "fair"}  # "model": our 0.60 vs the market's 0.55 in the NFL


def test_an_altitude_over_at_a_neutral_site_is_pulled():
    """Owner, Nov 14: SDSU vs Northern Colorado was in Rapid City, SD, not Greeley."""
    from types import SimpleNamespace

    from sportsedge.runner import _pull_neutral_altitude

    c = card(make_pick(market="totals", side="over", point=152.5, american=-105, event_id="g1"), units=1.0)
    c.headline = "Over 152.5 at -105: road team visiting altitude."
    led = Ledger()
    led.publish([c], NOW - timedelta(hours=6))
    scan = SimpleNamespace(contexts={"g1": SimpleNamespace(neutral=True)})
    assert len(_pull_neutral_altitude(led, [scan], NOW)) == 1
    assert next(iter(led.entries.values())).status == "void"
