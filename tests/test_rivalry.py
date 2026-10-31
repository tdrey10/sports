"""Owner, Oct 29: his picks vs our leans, graded the same way."""

import csv
from datetime import datetime, timedelta, timezone

from sportsedge import rivalry
from sportsedge.ledger import Ledger
from sportsedge.models.ratings import GameResult

NOW = datetime(2026, 10, 30, 15, tzinfo=timezone.utc)
START = NOW + timedelta(hours=8)


def snapshots(root):
    (root / "odds" / "nba").mkdir(parents=True)
    with (root / "odds" / "nba" / "2026-10.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["seen_at", "event_id", "start", "home", "away", "market", "side", "point", "book", "price"])
        w.writeheader()
        w.writerow({"seen_at": NOW.isoformat(), "event_id": "g1", "start": START.isoformat(), "home": "Los Angeles Lakers",
                    "away": "Boston Celtics", "market": "h2h", "side": "home", "point": "", "book": "draftkings", "price": -120})


def test_your_pick_is_saved_on_the_right_game_and_graded_like_ours(tmp_path):
    snapshots(tmp_path)
    msg = rivalry.add(tmp_path, "nba", "Lakers", -120, NOW)
    assert msg.startswith("Saved: Los Angeles Lakers ML at -120")
    assert rivalry.add(tmp_path, "nba", "Lakers", -120, NOW).startswith("Already have it")
    L = Ledger.load(rivalry.path(tmp_path))
    final = GameResult("g1", START, "Los Angeles Lakers", "Boston Celtics", 110, 100, state="post")
    L.refresh(lambda s, a, b: [final], lambda s, g: [], START + timedelta(hours=4))
    L.save(rivalry.path(tmp_path))
    board = rivalry.scoreboard(Ledger(), tmp_path)
    assert board["you"]["won"] == 1 and abs(board["you"]["units"] - 100 / 120) < 0.01
    assert board["your_picks"][0]["status"] == "won"


def test_a_team_with_no_game_coming_up_says_so(tmp_path):
    snapshots(tmp_path)
    try:
        rivalry.add(tmp_path, "nba", "Knicks", 150, NOW)
    except ValueError as exc:
        assert "no upcoming NBA game" in str(exc)
    else:
        raise AssertionError("should not save a pick without a game")


def test_a_pick_sent_in_chat_waits_in_the_inbox_and_is_filed_once(tmp_path):
    snapshots(tmp_path)
    assert rivalry.send(tmp_path, "nba", "Lakers", -120, NOW).startswith("In: Los Angeles Lakers")
    assert rivalry._file_inbox(tmp_path) == 1 and rivalry._file_inbox(tmp_path) == 0
    assert len(Ledger.load(rivalry.path(tmp_path)).entries) == 1


def test_the_doctors_parlay_is_filed_and_settles_on_its_legs(tmp_path):
    """Owner, Oct 29: 'Also track my parlay I give you. Can you call it The Doctor vs Grim.'"""
    snapshots(tmp_path)
    with (tmp_path / "odds" / "nba" / "2026-10.csv").open("a", newline="") as f:
        csv.writer(f).writerow([NOW.isoformat(), "g2", START.isoformat(), "Miami Heat", "Atlanta Hawks", "h2h", "home", "",
                                "draftkings", -150])
    legs = [rivalry.parse_leg("nba|Lakers|-120"), rivalry.parse_leg("nba|Heat|-150")]
    assert rivalry.send_parlay(tmp_path, legs, NOW).startswith("In: 2-leg parlay")
    assert rivalry._file_parlays(tmp_path) == 1 and rivalry._file_parlays(tmp_path) == 0
    L = Ledger.load(tmp_path / rivalry.LEGS)
    finals = [GameResult("g1", START, "Los Angeles Lakers", "Boston Celtics", 110, 100, state="post"),
              GameResult("g2", START, "Miami Heat", "Atlanta Hawks", 99, 101, state="post")]
    L.refresh(lambda s, a, b: finals, lambda s, g: [], START + timedelta(hours=4))
    L.save(tmp_path / rivalry.LEGS)
    board = rivalry.scoreboard(Ledger(), tmp_path)
    assert board["names"] == {"you": "The Doctor", "us": "Grim"}
    assert board["parlays"]["you"]["lost"] == 1 and board["your_parlays"][0]["status"] == "lost"
    assert rivalry.parse_leg("nfl|Bills|-110|spread -3.5")[3:5] == ("spreads", -3.5)


def test_a_changed_pick_is_taken_back_before_its_game(tmp_path):
    """Owner, Oct 29: "I'm gonna change my pick. Panthers +3 1/2 instead of Packers.\""""
    snapshots(tmp_path)
    rivalry.send(tmp_path, "nba", "Lakers", -110, NOW, "spreads", -3.5)
    rivalry._file_inbox(tmp_path)
    rivalry.cancel(tmp_path, "nba", "Lakers", NOW + timedelta(hours=1), "spreads")
    rivalry.send(tmp_path, "nba", "Celtics", -110, NOW + timedelta(hours=1), "spreads", 3.5)
    rivalry._file_inbox(tmp_path)
    picks = {e.selection: e.status for e in Ledger.load(rivalry.path(tmp_path)).entries.values()}
    assert picks == {"Los Angeles Lakers -3.5": "void", "Boston Celtics +3.5": "pending"}
    rivalry._file_inbox(tmp_path)  # filing again changes nothing
    assert {e.selection: e.status for e in Ledger.load(rivalry.path(tmp_path)).entries.values()} == picks


def test_grim_posts_the_money_plays_and_one_best_pick_on_a_day_without_one():
    """Owner, Oct 31: 'Have Grim post only the money plays but there has to be picks every day.'"""
    from tests.test_ledger import card, make_pick

    L = Ledger()
    day1 = datetime(2026, 11, 2, 18, tzinfo=timezone.utc)
    day2 = day1 + timedelta(days=1)
    money = make_pick(market="h2h", point=None, american=120, event_id="m1", win_prob=0.5, market_prob=0.45)
    lean_a = make_pick(market="h2h", point=None, american=-110, event_id="l1", win_prob=0.52, market_prob=0.51)
    lean_b = make_pick(market="h2h", point=None, american=-110, event_id="l2", win_prob=0.55, market_prob=0.50)
    lean_c = make_pick(market="h2h", point=None, american=-110, event_id="l3", win_prob=0.6, market_prob=0.6)
    import dataclasses
    picks = [dataclasses.replace(money, commence_time=day1), dataclasses.replace(lean_a, commence_time=day1),
             dataclasses.replace(lean_b, commence_time=day2), dataclasses.replace(lean_c, commence_time=day2)]
    L.publish([card(picks[0], units=0.5)] + [card(p, tier="lean", units=0.0) for p in picks[1:]], day1 - timedelta(days=1))
    got = sorted(e.event_id for e in rivalry.grim_picks(L))
    assert got == ["l2", "m1"]  # day 1: the money play only; day 2: the lean that beats the market by the most
