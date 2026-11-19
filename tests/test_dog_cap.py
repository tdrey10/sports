"""Owner's rule: no pregame dog above +300 on any board (Nov 19: the D503 night lean posted Colts +525)."""

from datetime import datetime, timedelta, timezone

from sportsedge import sharp, tiers
from sportsedge.d5.board import night_picks
from sportsedge.edge import Settings
from sportsedge.ledger import ENGINE_MARK, Ledger
from sportsedge.sports import SPORTS
from tests.test_d5 import side
from tests.test_ledger import START, card, make_pick
from tests.test_sharp import EVENT, NOW as SHARP_NOW, book


def test_cap_is_pregame_moneylines_only():
    assert tiers.past_dog_cap("h2h", 301, "lean") and tiers.past_dog_cap("h2h", 525, "value")
    assert not tiers.past_dog_cap("h2h", 300, "lean")
    assert not tiers.past_dog_cap("h2h", 900, "live_plus")  # live plus money has its own +2000 cap
    assert not tiers.past_dog_cap("spreads", 400, "value") and not tiers.past_dog_cap("h2h", None, "lean")
    assert sharp.MAX_DOG == tiers.DAILY_MAX_DOG


def test_night_lean_never_posts_a_long_shot():
    thursday = datetime(2026, 11, 19, 18, 0, tzinfo=timezone.utc)
    start = thursday + timedelta(hours=7)
    fav = side(-700, 0.85, 0.85, sport="nfl", eid="n", start=start, reasons=())  # too short for MAX_FAV
    dog = side(525, 0.15, 0.15, sport="nfl", eid="n", side="away", start=start, reasons=())
    assert night_picks([fav, dog], thursday, [], set()) == []
    ok = side(250, 0.30, 0.30, sport="nfl", eid="m", side="away", start=start, reasons=())
    assert [s.odds for _, _, s, _ in night_picks([fav, ok], thursday, [], set())] == [250]


def test_open_long_shot_formula_lean_is_pulled():
    now = START - timedelta(hours=3)
    led = Ledger()
    c = card(make_pick(market="h2h", point=None, american=525, win_prob=0.15, event_id="c"), tier="lean", units=0.0)
    c.reasons.append(ENGINE_MARK)  # formula picks skip most checks; not this one
    ok = card(make_pick(market="h2h", point=None, american=250, win_prob=0.47, event_id="d"), tier="lean", units=0.0)
    led.publish([c, ok], now)
    pulled = led.withdraw(Settings(), now)
    assert [e.odds for e in pulled] == [525]
    assert pulled[0].reasons[0].startswith("Withdrawn before the game: +525 is past our +300 cap")


def test_sharp_value_skips_dogs_past_the_cap():
    ev = dict(EVENT, bookmakers=[book("pinnacle", -400, 360), book("fanduel", -450, 400)])  # +400 beats fair by 3%
    assert not [v for v in sharp.find_value(SPORTS["nhl"], ev, SHARP_NOW) if v["side"] == "away"]
