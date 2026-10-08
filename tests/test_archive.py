import csv
from datetime import datetime, timedelta, timezone

from sportsedge import archive
from sportsedge.data.base import Event, Quote
from sportsedge.data.sample import SampleProvider
from sportsedge.edge import Settings
from sportsedge.engine import Engine
from sportsedge.models.ratings import GameResult
from sportsedge.sports import resolve


def test_games_csv_one_file_per_month_with_close_and_open(tmp_path):
    games = [
        GameResult("a", datetime(2026, 9, 30, tzinfo=timezone.utc), "Home", "Away", 24, 20),
        GameResult("b", datetime(2026, 10, 2, tzinfo=timezone.utc), "Home", "Away", 10, 13),
        GameResult("c", datetime(2026, 10, 9, tzinfo=timezone.utc), "Home", "Away", None, None),  # not played
    ]
    close = {"a": [Quote("h2h", "home", None, "draftkings", -150), Quote("h2h", "away", None, "draftkings", 130),
                   Quote("spreads", "home", -3.5, "draftkings", -110), Quote("spreads", "away", 3.5, "draftkings", -110),
                   Quote("totals", "over", 44.5, "draftkings", -105), Quote("totals", "under", 44.5, "draftkings", -115)]}
    opened = {"a": [Quote("h2h", "home", None, "draftkings", -135), Quote("h2h", "away", None, "draftkings", 115)]}
    assert archive.write_games(tmp_path, "nfl", games, close, opened) == 2
    rows = list(csv.DictReader(open(tmp_path / "nfl" / "2026-09.csv")))
    assert rows[0]["close_home_ml"] == "-150" and rows[0]["close_home_spread"] == "-3.5"
    assert rows[0]["close_total"] == "44.5" and rows[0]["open_home_ml"] == "-135"
    october = list(csv.DictReader(open(tmp_path / "nfl" / "2026-10.csv")))
    assert [r["event_id"] for r in october] == ["b"] and october[0]["close_home_ml"] == ""


def test_scan_snapshots_and_prop_lines_are_logged(tmp_path):
    provider = SampleProvider(seed=5)
    engine = Engine(provider, Settings(), now=provider.now)
    scans = [engine.scan(c, props=True) for c in resolve("nba")]
    n = archive.log_snapshots(tmp_path / "odds", scans, provider.now)
    p = archive.log_prop_lines(tmp_path / "props", scans, provider.now)
    assert n > 0 and p > 0
    snaps = list(csv.DictReader(open(next((tmp_path / "odds" / "nba").glob("*.csv")))))
    assert len(snaps) == n and {r["market"] for r in snaps} >= {"h2h", "totals"}
    props = list(csv.DictReader(open(next((tmp_path / "props" / "nba").glob("*.csv")))))
    assert len(props) == p and all(r["over_price"] and r["under_price"] for r in props)
    # A second scan with the same lines adds nothing (only moves are logged) and keeps one header.
    assert archive.log_snapshots(tmp_path / "odds", scans, provider.now) == 0
    text = open(next((tmp_path / "odds" / "nba").glob("*.csv"))).read()
    assert text.count("seen_at") == 1


def test_logged_prop_lines_are_graded_once_the_game_is_over(tmp_path):
    from sportsedge.models.props import PlayerGame
    from sportsedge.sports import SPORTS

    start = datetime(2026, 10, 1, 0, 0, tzinfo=timezone.utc)
    ev = Event(id="g1", sport="nba", commence_time=start, home="Home", away="Away")

    class Scan:
        def __init__(self, line, seen):
            self.sport = SPORTS["nba"]
            self.prop_lines = [(ev, [Quote("player_points", "over", line, "draftkings", -115, "Star Player"),
                                     Quote("player_points", "under", line, "draftkings", -115, "Star Player")])]
            self.seen = seen

    archive.log_prop_lines(tmp_path / "props", [Scan(24.5, None)], start.replace(hour=0) - timedelta(hours=8))
    archive.log_prop_lines(tmp_path / "props", [Scan(25.5, None)], start - timedelta(hours=1))
    game = GameResult("g1", start, "Home", "Away", 110, 100)
    box = [PlayerGame("g1", start, "Star Player", "Home", {"PTS": 31.0})]
    n = archive.grade_prop_lines(tmp_path / "props", tmp_path / "graded", [SPORTS["nba"]],
                                 lambda cfg, a, b: [game], lambda cfg, g: box, start + timedelta(days=1))
    assert n == 1
    row = next(csv.DictReader(open(tmp_path / "graded" / "nba" / "2026-10.csv")))
    assert (row["open_line"], row["close_line"], row["actual"], row["result"]) == ("24.5", "25.5", "31.0", "over")
    # Never graded twice.
    assert archive.grade_prop_lines(tmp_path / "props", tmp_path / "graded", [SPORTS["nba"]],
                                    lambda cfg, a, b: [game], lambda cfg, g: box, start + timedelta(days=1)) == 0
    summary = archive.prop_summary(tmp_path / "graded")
    assert summary["nba"]["player_points"] == {"lines": 1, "over": 1, "under": 0, "push": 0, "void": 0, "moved": 1}
