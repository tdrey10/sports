import csv
from datetime import datetime, timezone

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
    # Appending a second scan keeps one header.
    archive.log_snapshots(tmp_path / "odds", scans, provider.now)
    text = open(next((tmp_path / "odds" / "nba").glob("*.csv"))).read()
    assert text.count("seen_at") == 1
