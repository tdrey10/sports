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
    new = led.post_parlays(now)
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


def test_short_board_fills_with_likely_winners_from_mid_morning():
    from sportsedge.parlays import build

    now = START - timedelta(hours=6)
    led = Ledger()
    picks = [make_pick(event_id=f"g{i}", american=-120, win_prob=0.55 + i * 0.01) for i in range(9)]
    led.publish([card(p, tier="lean", units=0.0) for p in picks], now)
    today = led.day_of(now)
    assert build(led.entries.values(), [], now, today, local_hour=6) == []  # early: wait for unit plays
    made = build(led.entries.values(), [], now, today, local_hour=10)
    assert [p.size for p in made] == [2, 3, 4, 8] and all(p.units == 0 for p in made)
    top = [led.entries[i].win_prob for i in made[0].legs]
    assert [round(x, 2) for x in top] == [0.63, 0.62]  # the most likely winners first
