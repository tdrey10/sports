import csv
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from sportsedge import watch

NOW = datetime(2026, 11, 10, 18, tzinfo=timezone.utc)


def _box(root, games):
    """games: (event_id, iso date, home, away, home pts, away pts)."""
    (root / "boxscores" / "ncaab").mkdir(parents=True)
    (root / "games" / "ncaab").mkdir(parents=True)
    box = {}
    with (root / "games" / "ncaab" / "2026-11.csv").open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["event_id", "date", "home", "away", "home_score", "away_score"])
        for eid, d, h, a, hp, ap in games:
            w.writerow([eid, d, h, a, hp, ap])
            line = {"fga": 60.0, "oreb": 10.0, "tov": 12.0, "fta": 20.0}
            if hp == "":
                continue
            box[eid] = {"date": d, "home": {**line, "pts": float(hp)}, "away": {**line, "pts": float(ap)}}
    (root / "boxscores" / "ncaab" / "2026-11.json").write_text(json.dumps(box))


def test_projection_needs_five_games_and_tracks_scoring(tmp_path):
    games = []
    for i in range(6):
        d = (NOW - timedelta(days=10 - i)).isoformat()
        games.append((f"a{i}", d, "Fast U", "Filler A", 95, 85))
        games.append((f"b{i}", d, "Slow U", "Filler B", 60, 55))
    _box(tmp_path, games)
    cur, prior, lp, lppp = watch.team_ratings(tmp_path, 2026)
    assert cur["Fast U"][0] == 6
    fast = watch.project("Fast U", "Filler A", cur, prior, lp, lppp)
    slow = watch.project("Slow U", "Filler B", cur, prior, lp, lppp)
    assert fast > slow
    assert watch.project("Fast U", "Nobody", cur, prior, lp, lppp) is None


def _scan(eid, start, neutral, point=140.5):
    pick = lambda side: SimpleNamespace(event_id=eid, market="totals", side=side, player=None, point=point,  # noqa: E731
                                        american=-110, book="draftkings")
    ctx = SimpleNamespace(event=SimpleNamespace(commence_time=start), home="Home U", away="Road U", neutral=neutral)
    return SimpleNamespace(sport=SimpleNamespace(key="ncaab"), contexts={eid: ctx}, picks=[pick("over"), pick("under")])


def test_neutral_site_posts_the_under_once_and_grades(tmp_path):
    _box(tmp_path, [("g1", (NOW + timedelta(hours=3)).isoformat(), "Home U", "Road U", "", "")])
    scan = _scan("g1", NOW + timedelta(hours=3), neutral=True)
    picks = watch.update([scan], NOW, tmp_path)
    assert [(p["rule"], p["side"], p["total"]) for p in picks] == [("neutral", "under", 140.5)]
    assert watch.update([scan], NOW, tmp_path) == picks  # no duplicate on the next run
    # Final comes in: 70-65 = 135, under 140.5 wins.
    with (tmp_path / "games" / "ncaab" / "2026-11.csv").open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["event_id", "date", "home", "away", "home_score", "away_score"])
        w.writerow(["g1", NOW.isoformat(), "Home U", "Road U", 70, 65])
    picks = watch.update([], NOW + timedelta(hours=6), tmp_path)
    assert picks[0]["status"] == "won" and picks[0]["final"] == 135
    s = watch.summary(picks, NOW + timedelta(hours=6))
    assert s["records"]["neutral"]["won"] == 1 and s["records"]["neutral"]["units"] > 0.9


def test_home_game_without_projection_posts_nothing(tmp_path):
    _box(tmp_path, [])
    assert watch.find([_scan("g2", NOW + timedelta(hours=3), neutral=False)], NOW, tmp_path) == []
