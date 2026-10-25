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


def test_qb_key_is_his_adjusted_yards_per_attempt():
    from types import SimpleNamespace as NS

    from sportsedge.d5.qb import QBIndex, starters

    rows = [NS(team="A", player="Starter", stats={"passing.C/ATT_A": 30, "passing.YDS": 300, "passing.TD": 2, "passing.INT": 0}),
            NS(team="A", player="Backup", stats={"passing.C/ATT_A": 3, "passing.YDS": 20}),
            NS(team="B", player="Other", stats={"passing.C/ATT_A": 30, "passing.YDS": 200, "passing.TD": 1, "passing.INT": 1}),
            NS(team="B", player="Runner", stats={"rushing.YDS": 90})]
    qbs = starters(rows)
    assert qbs["A"][0] == "Starter" and qbs["B"][0] == "Other"
    idx = QBIndex({"g1": {"date": "2026-10-10T19:00:00+00:00", "teams": qbs}})
    a = (300 + 40 + 480) / 110
    b = (200 + 20 - 45 + 480) / 110
    assert abs(idx.key_edge("A", "B", NOW) - (a - b) / 2) < 1e-9
    assert idx.key_edge("A", "Unknown", NOW) == 0.0  # a QB we haven't seen is never guessed
    assert idx.key_edge("A", "B", datetime(2026, 10, 1, tzinfo=timezone.utc)) == 0.0  # only starts before the game


def test_his_published_blend_and_weights_are_used():
    from sportsedge.d5.features import FEATURES
    from sportsedge.d5.model import SportModel, published

    m = published(SportModel("ncaaf", 37.5, 100, [0.0] * (len(FEATURES) + 1), blend=[0.0, 0.0, 0.0, 0.11]))
    assert m.blend == [0.0, 0.0112, -0.0651, 0.2029] and m.fit_blend == [0.0, 0.0, 0.0, 0.11]
    assert m.w[1 + FEATURES.index("key")] == 0.309
    assert published(m).fit_blend == [0.0, 0.0, 0.0, 0.11]  # applying it twice keeps our own fit


def test_dog_score_bye_and_ice_cold_fire():
    from types import SimpleNamespace as NS

    from sportsedge.d5.board import _dog_scores
    from sportsedge.d5.features import TeamState

    dog = side(310, 0.288, 0.288, p_market=0.234, sport="ncaaf", eid="i", side="home")
    fav = side(-395, 0.712, 0.712, p_market=0.766, sport="ncaaf", eid="i", side="away")
    dog.team, fav.team = "Illinois", "Oregon"
    td = TeamState(last=dog.start - timedelta(days=14), last_won=True, margins=[20, 15, -10, -12, 1], streak=1)
    tf = TeamState(last=dog.start - timedelta(days=7), last_won=True, margins=[10, 7], streak=2)
    _dog_scores([dog, fav], NS(teams={"Illinois": td, "Oregon": tf}))
    notes = " ".join(dog.notes)
    assert "off a bye" in notes  # 14 days off (the old check read rest capped at 10 and never fired)
    assert "ice cold" in notes
    assert round(dog.dog_score, 2) == round(5.4 - 1, 2)  # his Illinois: +5.4 base, +2 bye, -3 ice cold = 4.4
    assert dog.dog_p is not None  # past the college gate of 4
