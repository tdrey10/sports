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
