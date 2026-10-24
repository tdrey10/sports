"""The D503 engine (owner, Oct 24): rebuilt from its published spec."""

from datetime import datetime, timedelta, timezone

from sportsedge.d5.board import Side, choose, choose_early, dog_units, early_spots, lock_units, night_picks
from sportsedge.d5.model import dec, no_vig
from tests.test_ledger import make_pick

NOW = datetime(2026, 10, 24, 15, 0, tzinfo=timezone.utc)  # Saturday 8 AM PT


def side(odds, p, own, p_market=None, sport="nhl", eid="g1", side="home", reasons=("the stronger team",),
         start=None, **kw):
    start = start or NOW + timedelta(hours=8)
    pm = p_market if p_market is not None else 1 / dec(odds)
    pk = make_pick(market="h2h", american=odds, point=None, side=side, event_id=eid, sport=sport, market_prob=pm)
    s = Side(sport, eid, "Home", "Away", side, "Home" if side == "home" else "Away", "Away", start, pk, odds, pm, p,
             own, own, 20.0, 0.0, {}, 1 if side == "home" else -1, reasons=list(reasons), team_games=10, **kw)
    return s


def test_no_vig_and_decimal():
    assert round(no_vig(-150, 130), 3) == 0.58 and dec(-150) == 1 + 100 / 150


def test_lock_sizing_ladder():
    thin = side(-140, 0.60, 0.59)  # own beats -140 (58.3%) by under 3%
    assert lock_units(thin) == 0.5
    strong = side(-140, 0.64, 0.64)
    assert lock_units(strong) == 1.0  # no weighed read: 1u
    strong.w_p = 0.64
    assert lock_units(strong) == 3.0  # weighed and own read both beat the price: Max Play


def test_lock_never_past_minus_150_and_never_forced():
    too_short = side(-160, 0.68, 0.68, eid="a")
    fair = side(-140, 0.58, 0.58, eid="b")  # no edge
    board = choose([too_short, fair], NOW)
    assert not [t for t, *_ in board if t == "lock"]
    assert all(s.odds >= -150 for _, _, s, _ in board)


def test_dog_needs_the_gate_and_gets_two_units():
    dog = side(140, 0.46, 0.46, p_market=0.40, sport="nfl", eid="d", side="away")
    dog.dog_score, dog.dog_p = 9.0, 0.49  # past the NFL gate of 8
    board = choose([dog], NOW)
    assert [(t, u) for t, u, *_ in board] == [("dog", 2.0)]
    assert dog_units(side(120, 0.5, 0.5, sport="nba")) == 0.5  # NBA dogs are ½u
    ungated = side(140, 0.46, 0.46, p_market=0.40, sport="nfl", eid="e", side="away")
    assert not [t for t, *_ in choose([ungated], NOW) if t == "dog"]


def test_early_spots_bye_and_both_sides_cancel():
    later = NOW + timedelta(days=1)  # Sunday
    bye = side(130, 0.46, 0.46, p_market=0.43, sport="nfl", eid="x", side="away", start=later, rest=14, rest_opp=7)
    hit, total = early_spots(bye)
    assert hit == ["bye"] and total >= 0.05
    assert [(t, u) for t, u, *_ in choose_early([bye], NOW)] == [("early", 1.0)]
    other = side(130, 0.46, 0.46, p_market=0.43, sport="nfl", eid="x", side="home", start=later, rest=14, rest_opp=7)
    assert choose_early([bye, other], NOW) == []
    same_day = side(130, 0.46, 0.46, p_market=0.43, sport="nfl", eid="y", start=NOW + timedelta(hours=5),
                    rest=14, rest_opp=7)
    assert choose_early([same_day], NOW) == []  # never on the game's own date


def test_every_nfl_game_gets_a_pick_on_monday_night():
    monday = datetime(2026, 10, 26, 18, 0, tzinfo=timezone.utc)
    a = side(-120, 0.53, 0.53, sport="nfl", eid="m", start=monday + timedelta(hours=6), reasons=())
    b = side(100, 0.47, 0.47, sport="nfl", eid="m", side="away", start=monday + timedelta(hours=6), reasons=())
    picks = night_picks([a, b], monday, [], set())
    assert len(picks) == 1 and picks[0][0] == "lean"


def test_elo_matches_the_spec_on_our_history():
    from sportsedge.d5.data import load
    from sportsedge.d5.features import tune_elo

    assert tune_elo(load("nfl"), "nfl") == (20, 40)  # his model.json: nfl K 20 HFA 40
