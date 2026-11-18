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


def test_the_lock_slot_is_minus_120_to_minus_170_and_heads_must_beat_break_even():
    """Bug fix, Nov 18: the promoter took favorites from -101 to -150 (a -110 coin flip could be the Lock; the
    owner's Lock is -170 to -120) and leans within a point under the price, which withdraw then pulled."""
    led = _ledger(_lean("flip", -110, 0.56, 0.50, 0.06), _lean("deep", -165, 0.66, 0.61, 0.06))
    assert {e.tier: e.event_id for e in daily_lock_dog(led, NOW)} == {"lock": "deep"}
    led = _ledger(_lean("under", -140, 0.58, 0.57, -0.005), _lean("dog", 140, 0.41, 0.40, -0.006))
    assert daily_lock_dog(led, NOW) == []  # 58% at -140 needs 58.3%; 41% at +140 needs 41.7%
    led = _ledger(_lean("dog", 215, 0.40, 0.31, 0.26))
    assert [e.tier for e in daily_lock_dog(led, NOW)] == ["dog"]  # +220 is in the Dog range now


def _board():
    from sportsedge.data.sample import SampleProvider
    from sportsedge.edge import Settings
    from sportsedge.engine import Engine
    from sportsedge.sports import resolve

    provider = SampleProvider(seed=5)
    settings = Settings(min_ev=0.5)  # nothing clears the bar for units
    engine = Engine(provider, settings, now=provider.now)
    scans = [engine.scan(c) for c in resolve("nba,nhl,mlb")]
    dogs = []
    for scan in scans:
        for p in scan.picks:
            p.ev = -0.02  # nothing beats its price...
            p.warnings = []
            if p.market == "h2h" and 100 <= p.american <= 220 and p.market_prob is not None:
                dogs.append(p)
    return provider, settings, scans, dogs


def test_the_board_posts_its_best_live_dog_when_no_lean_can_be_the_dog():
    """Bug fix, Nov 18: the Dog of the Day came only from posted leans, nearly all favorites, so Nov 17 and 18
    had no Dog. Owner: a Dog every day, the most likely dog on the board to win outright, 40%+, at a fair price."""
    from sportsedge.ledger import break_even
    from sportsedge.tiers import build_cards

    provider, settings, scans, dogs = _board()
    assert len(dogs) >= 3
    for p in dogs:
        p.win_prob = break_even(p.american) - 0.01  # ...and no dog beats its price
    best, fair, shy = dogs[0], dogs[1], dogs[2]
    best.win_prob, best.ev = 0.44, 0.44 * (1 + best.american / 100) - 1  # beats any +100..+220 price at 44%
    fair.win_prob = break_even(fair.american) + 0.005
    shy.american, shy.win_prob = 160, 0.39  # beats +160 (38.5%) but under the 40% floor
    if fair.win_prob >= best.win_prob:
        fair.win_prob = best.win_prob - 0.001
    taken = {"lock", "dog"}  # the runner always hands build_cards these (Lock/Dog come from elsewhere)
    cards = build_cards(scans, settings, provider.now, taken_today=taken, live_dog=lambda p: True)
    live = [c for c in cards if any(r.startswith("Best live dog on the board") for r in c.reasons)]
    assert len(live) == 1 and live[0].pick is best
    assert live[0].tier == "lean" and live[0].units == 0
    # Without the request (a Dog is already up today) the board posts no extra dog.
    assert not [c for c in build_cards(scans, settings, provider.now, taken_today=taken)
                if any(r.startswith("Best live dog") for r in c.reasons)]
    # Only today's games: the request's test is applied.
    assert not [c for c in build_cards(scans, settings, provider.now, taken_today=taken, live_dog=lambda p: False)
                if any(r.startswith("Best live dog") for r in c.reasons)]
    # Nothing at 40%+ that beats its price: no Dog at all (never a dog we think loses).
    best.win_prob = 0.39
    fair.win_prob = break_even(fair.american) - 0.01
    assert not [c for c in build_cards(scans, settings, provider.now, taken_today=taken, live_dog=lambda p: True)
                if any(r.startswith("Best live dog") for r in c.reasons)]


def test_the_live_dog_lean_becomes_the_dog_of_the_day_and_stays_up():
    from sportsedge.edge import Settings
    from sportsedge.ledger import break_even
    from sportsedge.tiers import build_cards

    provider, settings, scans, dogs = _board()
    for p in dogs:
        p.win_prob = break_even(p.american) - 0.01
    now = provider.now
    led = Ledger()
    today = [p for p in dogs if led.day_of(p.commence_time) == led.day_of(now) and p.commence_time > now]
    assert today
    best = today[0]
    best.win_prob = 0.44
    cards = build_cards(scans, settings, now, taken_today={"lock", "dog"},
                        live_dog=lambda p: led.day_of(p.commence_time) == led.day_of(now))
    led.publish(cards, now)
    made = {e.tier: e for e in daily_lock_dog(led, now) if e.tier == "dog"}
    assert made["dog"].event_id == best.event_id and made["dog"].units == 0
    assert made["dog"].headline.startswith("Dog of the Day, no money")
    assert any(r.startswith("Best live dog on the board") for r in made["dog"].reasons)
    led.withdraw(Settings(), now)
    assert made["dog"].status == "pending"  # 44% beats its price: withdraw keeps it
