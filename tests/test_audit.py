import csv
import json
from datetime import datetime

from sportsedge import archive, audit


def _games(folder, sport, month, rows):
    path = folder / "games" / sport / f"{month}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=archive.GAME_FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in archive.GAME_FIELDS})


def test_season_rolls_over_at_the_sport_start_month():
    assert audit.season("nba", datetime(2026, 6, 1)) == 2025
    assert audit.season("nba", datetime(2026, 10, 25)) == 2026
    assert audit.season("mlb", datetime(2026, 10, 2)) == 2026
    assert audit.season("nfl", datetime(2027, 1, 10)) == 2026
    assert audit.season("epl", datetime(2026, 8, 15)) == 2026


def test_games_without_a_closing_line_are_counted_but_not_testable(tmp_path):
    lined = [{"event_id": str(i), "date": "2025-11-02T00:00:00+00:00", "close_home_ml": "-120",
              "close_total": "220.5", "open_home_ml": "-110"} for i in range(250)]
    bare = [{"event_id": f"b{i}", "date": "2024-11-02T00:00:00+00:00"} for i in range(400)]
    _games(tmp_path, "nba", "2025-11", lined)
    _games(tmp_path, "nba", "2024-11", bare)
    a = audit.audit_sport(tmp_path, "nba")
    assert (a.games, a.closing, a.opening) == (650, 250, 250)
    assert a.by_season == {2024: (400, 0), 2025: (250, 250)}
    assert a.first_line == "2025-11"
    assert a.verdict == "too thin to test"  # 250 closing lines < MIN_TESTABLE


def test_verdict_needs_two_seasons_of_lines_for_a_blind_season_test(tmp_path):
    row = lambda i, d: {"event_id": f"{d}{i}", "date": d, "close_home_ml": "+105"}  # noqa: E731
    _games(tmp_path, "nhl", "2025-11", [row(i, "2025-11-05T00:00:00+00:00") for i in range(350)])
    assert audit.audit_sport(tmp_path, "nhl").verdict == "one season of lines: time-split test only"
    _games(tmp_path, "nhl", "2026-10", [row(i, "2026-10-05T00:00:00+00:00") for i in range(250)])
    assert audit.audit_sport(tmp_path, "nhl").verdict == "blind test on an unseen season"


def test_snapshots_count_line_moves_and_ignore_prices_seen_after_the_start(tmp_path):
    path = tmp_path / "odds" / "nfl" / "2026-10.csv"
    path.parent.mkdir(parents=True)
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=archive.SNAPSHOT_FIELDS)
        w.writeheader()
        start = "2026-10-11T17:00:00+00:00"
        for seen, ev, price in [("2026-10-06", "a", 120), ("2026-10-09", "a", 110),  # moved
                                ("2026-10-06", "b", 150), ("2026-10-12", "b", 300)]:  # move only after kickoff
            w.writerow({"seen_at": seen + "T12:00:00+00:00", "event_id": ev, "start": start, "home": "H",
                        "away": "A", "market": "h2h", "side": "away", "point": "", "book": "draftkings",
                        "price": price})
    a = audit.audit_sport(tmp_path, "nfl")
    assert (a.snapshot_rows, a.snapshot_games, a.snapshot_moved) == (4, 2, 1)


def test_assumed_prop_prices_are_not_counted_as_real(tmp_path):
    path = tmp_path / "props" / "nfl" / "2026-10.csv"
    path.parent.mkdir(parents=True)
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=archive.PROP_FIELDS)
        w.writeheader()
        base = {"seen_at": "x", "event_id": "1", "start": "y", "home": "H", "away": "A", "player": "P",
                "market": "player_pass_yds", "line": "250.5", "over_price": "-115", "under_price": "-115"}
        w.writerow({**base, "book": "draftkings_est"})
        w.writerow({**base, "book": "fanduel", "over_price": "-110"})
    a = audit.audit_sport(tmp_path, "nfl")
    assert (a.prop_lines, a.prop_real_price) == (2, 1)


def test_box_scores_with_stats_are_counted(tmp_path):
    folder = tmp_path / "boxscores" / "nba"
    folder.mkdir(parents=True)
    (folder / "2026-01.json").write_text(json.dumps({"1": {"date": "d", "home": {}, "away": {}},
                                                     "2": {"date": "d", "none": True}}))
    assert audit.audit_sport(tmp_path, "nba").box_scores == 1


def test_report_prints_every_sport(tmp_path):
    _games(tmp_path, "mlb", "2026-04", [{"event_id": "1", "date": "2026-04-01T00:00:00+00:00"}])
    text = audit.report(audit.audit(tmp_path))
    assert "mlb" in text and "too thin to test" in text
