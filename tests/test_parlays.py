from dataclasses import replace
from datetime import timedelta

from sportsedge.ledger import Ledger
from sportsedge.parlays import settle
from tests.test_ledger import START, card, make_pick


def board(n, now):
    picks = [make_pick(event_id=f"ev{i}", american=-110, win_prob=0.6 - i * 0.01) for i in range(n)]
    led = Ledger()
    led.publish([replace(card(p), best_rank=i + 1, score=80 - i) for i, p in enumerate(picks)], now)
    return led


def test_parlays_post_once_from_the_best_bets_one_leg_per_game():
    now = START - timedelta(hours=3)
    led = board(5, now)
    led.publish([card(make_pick(event_id="ev0", market="totals", side="over", point=44.5))], now)  # same game again
    new = [p for p in led.post_parlays(now) if p.kind == "heat"]
    assert [p.size for p in new] == [2, 3, 4]  # not enough games for an 8-team yet
    two = new[0]
    assert len(set(led.entries[i].event_id for i in two.legs)) == 2
    # Most likely to win first (ev0 is 60%, ev1 59%, ...), whatever the rank.
    assert [led.entries[i].event_id for i in new[2].legs] == ["ev0", "ev1", "ev2", "ev3"]
    likely = board(3, now)
    likely.entries[[k for k, e in likely.entries.items() if e.event_id == "ev2"][0]].win_prob = 0.7
    assert [likely.entries[i].event_id for i in likely.post_parlays(now)[0].legs] == ["ev2", "ev0"]
    assert abs(two.decimal - (1 + 100 / 110) ** 2) < 1e-3 and abs(two.hit_prob - 0.6 * 0.59) < 1e-3
    assert led.post_parlays(now + timedelta(minutes=30)) == []  # posted once a day, never reshuffled


def test_parlay_settles_from_its_legs(tmp_path):
    now = START - timedelta(hours=3)
    led = board(3, now)
    p = led.post_parlays(now)[0]
    legs = [led.entries[i] for i in p.legs]
    assert settle(p, led.entries)["status"] == "pending"
    legs[0].status, legs[0].graded_at = "lost", "x"
    assert settle(p, led.entries) == {"status": "lost", "profit": -0.5, "graded_at": "x"}
    legs[0].status = "won"
    legs[1].status, legs[1].graded_at = "push", "y"
    out = settle(p, led.entries)
    assert out["status"] == "won" and abs(out["profit"] - 0.5 * (1 + 100 / 110 - 1)) < 1e-3
    # Saved and loaded with the ledger, with its own record.
    led.save(tmp_path / "l.json")
    again = Ledger.load(tmp_path / "l.json")
    assert [q.size for q in again.parlays] == [2, 3]
    assert again.stats(START)["parlays"]["2"]["won"] == 1


def test_wanna_dabble_fills_with_leans_at_half_a_unit_and_the_heat_never_does():
    from sportsedge.parlays import DABBLE_STAKE, FILL_HOUR, build

    now = START - timedelta(hours=6)
    led = Ledger()
    picks = [make_pick(event_id=f"g{i}", american=-120, win_prob=0.55 + i * 0.01) for i in range(9)]
    led.publish([card(p, tier="lean", units=0.0) for p in picks], now)
    day = led.day_of(now)
    assert build(led.entries.values(), [], now, day, local_hour=FILL_HOUR - 1) == []  # too early: wait for real picks
    out = build(led.entries.values(), [], now, day, local_hour=FILL_HOUR)
    assert [(p.kind, p.size) for p in out] == [("dabble", n) for n in (2, 3, 4, 5, 8)]  # no Heat from leans
    assert all(p.units == DABBLE_STAKE == 0.5 for p in out)
    assert [led.entries[i].event_id for i in out[1].legs] == ["g8", "g7", "g6"]  # most likely winners first
    assert build(led.entries.values(), out, now, day, local_hour=FILL_HOUR + 2) == []  # posted once a day


def test_filler_skips_coin_flips_and_negative_edges_and_unit_plays_lead():
    from sportsedge.parlays import FILL_HOUR, build

    now = START - timedelta(hours=6)
    led = Ledger()
    led.publish([card(make_pick(event_id="u1", american=-110, win_prob=0.58))], now)
    led.publish([card(make_pick(event_id="l1", american=-130, win_prob=0.6), tier="lean", units=0.0),
                 card(make_pick(event_id="l2", american=120, win_prob=0.48), tier="lean", units=0.0)], now)
    out = build(led.entries.values(), [], now, led.day_of(now), local_hour=FILL_HOUR)
    assert [(p.kind, p.size) for p in out] == [("dabble", 2)]  # the 48% lean never fills a leg
    assert [led.entries[i].event_id for i in out[0].legs] == ["u1", "l1"]


def test_no_dabble_copy_of_a_heat_parlay_and_records_kept_apart():
    from sportsedge.parlays import FILL_HOUR, build

    now = START - timedelta(hours=3)
    led = board(5, now)  # five unit plays
    out = build(led.entries.values(), [], now, led.day_of(now), local_hour=FILL_HOUR)
    kinds = [(p.kind, p.size) for p in out]
    assert ("heat", 2) in kinds and ("dabble", 2) not in kinds  # same legs: only The Heat
    assert ("dabble", 5) in kinds  # no 5-team Heat, so the 5 unit plays make a Dabble
    led.parlays = out
    stats = led.stats(START)["parlays"]
    assert "2" in stats and "dabble5" in stats


def test_unstarted_dabble_parlay_survives_withdraw():
    from sportsedge.edge import Settings
    from sportsedge.parlays import FILL_HOUR

    now = START - timedelta(hours=6)
    led = Ledger()
    picks = [make_pick(event_id=f"g{i}", american=-120, win_prob=0.55 + i * 0.01) for i in range(3)]
    led.publish([card(p, tier="lean", units=0.0) for p in picks], now)
    led.post_parlays(now.replace(hour=FILL_HOUR + 7))  # START is UTC; local 10 AM+ in Pacific
    before = len(led.parlays)
    led.withdraw(Settings(), now)
    assert before and len(led.parlays) == before  # no-unit legs don't pull a Dabble parlay
