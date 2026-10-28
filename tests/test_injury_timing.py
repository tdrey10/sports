"""Owner, Oct 28: when a player is ruled out, do the books move far enough, fast enough?"""

import csv
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

from sportsedge import injurytiming
from sportsedge.archive import SNAPSHOT_FIELDS

T0 = datetime(2026, 10, 28, 15, tzinfo=timezone.utc)
START = T0 + timedelta(hours=8)


def scan(out=(), questionable=(), unknown=()):
    ctx = NS(event=NS(commence_time=START), home="Home", away="Away", injuries_unknown=list(unknown),
             injured={"Home": [(p, "G") for p in out]}, questionable={"Home": [(p, "F") for p in questionable]})
    return NS(sport=NS(key="nba", has_injury_feed=True), contexts={"g1": ctx})


def rows(tmp_path):
    with (tmp_path / "injuries" / "nba" / "2026-10.csv").open() as f:
        return list(csv.DictReader(f))


def test_only_changes_are_logged_and_a_first_look_is_not_news(tmp_path):
    out = tmp_path / "injuries"
    assert injurytiming.log(out, [scan(out=["Long Out"])], T0, root=tmp_path) == 0  # first look: a baseline
    assert injurytiming.log(out, [scan(out=["Long Out"], questionable=["Star"])], T0 + timedelta(minutes=30), root=tmp_path) == 1
    assert injurytiming.log(out, [scan(out=["Long Out", "Star"])], T0 + timedelta(hours=1), root=tmp_path) == 1
    assert injurytiming.log(out, [scan(out=["Star"])], T0 + timedelta(hours=2), root=tmp_path) == 1  # Long Out returns
    got = [(r["player"], r["was"], r["now"]) for r in rows(tmp_path)]
    assert got == [("Star", "active", "questionable"), ("Star", "questionable", "out"), ("Long Out", "out", "active")]


def test_a_team_with_no_report_is_not_logged(tmp_path):
    out = tmp_path / "injuries"
    injurytiming.log(out, [scan()], T0, root=tmp_path)
    assert injurytiming.log(out, [scan(out=["Star"], unknown=["Home"])], T0 + timedelta(hours=1), root=tmp_path) == 0


def _snap(w, at, home, away):
    for side, price in (("home", home), ("away", away)):
        w.writerow({"seen_at": at.isoformat(), "event_id": "g1", "start": START.isoformat(), "home": "Home",
                    "away": "Away", "market": "h2h", "side": side, "point": "", "book": "draftkings", "price": price})


def test_the_study_measures_the_move_on_the_news_and_after_it(tmp_path):
    out = tmp_path / "injuries"
    injurytiming.log(out, [scan()], T0, root=tmp_path)
    news = T0 + timedelta(hours=2)
    injurytiming.log(out, [scan(out=["Star"])], news, root=tmp_path)
    (tmp_path / "odds" / "nba").mkdir(parents=True)
    with (tmp_path / "odds" / "nba" / "2026-10.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=SNAPSHOT_FIELDS)
        w.writeheader()
        _snap(w, T0, -150, 130)  # before the news
        _snap(w, news, -130, 110)  # the book moved some when we saw it
        _snap(w, START - timedelta(minutes=10), -110, -110)  # and kept moving to the close
    (tmp_path / "games" / "nba").mkdir(parents=True)
    with (tmp_path / "games" / "nba" / "2026-10.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["event_id", "home_score", "away_score"])
        w.writeheader()
        w.writerow({"event_id": "g1", "home_score": 100, "away_score": 104})
    [e] = injurytiming.events("nba", tmp_path)
    assert e["news_move"] < 0 and e["drift"] < 0  # the injured team's chance fell on the news, then fell more
    assert e["bet_price"] == 110 and e["clv"] > 0 and e["ret"] == 1.1  # the other team at +110 beat the close and won
    res = injurytiming.run("nba", tmp_path)
    assert res["all"]["drift_same_way"] == 1.0 and res["all"]["verdict"] == "collecting"
    assert "kept moving the same way" in injurytiming.report(res)
