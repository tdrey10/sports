import csv
import gzip
from datetime import datetime, timedelta, timezone

from sportsedge import tennis, tennishist
from sportsedge.ledger import Ledger
from sportsedge.tennishist import Params

NOW = datetime(2026, 10, 12, 1, tzinfo=timezone.utc)


def _rows():
    rows = []
    t0 = NOW - timedelta(days=300)
    for i in range(30):  # Strong beats Weak 30 times; Mid splits with both
        rows.append({"id": f"atp:{i}", "tour": "atp", "start": (t0 + timedelta(days=i)).strftime("%Y-%m-%dT%H:%MZ"),
                     "p1_name": "Ana Strong", "p2_name": "Bo Weak", "winner": "1", "status": "STATUS_FINAL",
                     "surface": "hard", "bo": "3", "sets1": "6 6", "sets2": "2 3", "done": "1"})
        rows.append({"id": f"atp:m{i}", "tour": "atp", "start": (t0 + timedelta(days=i, hours=1)).strftime("%Y-%m-%dT%H:%MZ"),
                     "p1_name": "Cy Mid", "p2_name": "Bo Weak" if i % 2 else "Ana Strong", "winner": "1" if i % 2 else "2",
                     "status": "STATUS_FINAL", "surface": "hard", "bo": "3", "sets1": "6 6", "sets2": "4 4", "done": "1"})
    rows.append({"id": "atp:next", "tour": "atp", "start": (NOW + timedelta(hours=5)).strftime("%Y-%m-%dT%H:%MZ"),
                 "p1_name": "Ana Strong", "p2_name": "Bo Weak", "winner": "0", "status": "STATUS_SCHEDULED",
                 "surface": "clay", "bo": "3", "sets1": "", "sets2": "", "done": "0", "tourney": "Test Open", "round": "R32"})
    return rows


ODDS = {"lines": [{"a": "Bo Weak", "b": "Ana Strong\xa0", "tour": "atp", "start": (NOW + timedelta(hours=5)).strftime("%Y-%m-%dT%H:%MZ"),
                   "a_ml": 300, "b_ml": -400, "a_hcp": 4.5, "a_sp": -110},
                  {"a": "Old Match", "b": "Already Played", "tour": "atp", "start": "2026-10-01T00:00Z", "a_ml": 100, "b_ml": -120}]}


def test_names_match_across_sources():
    assert tennis.norm("Pablo Carreño-Busta") == tennis.norm("pablo carreno busta")
    assert tennis.norm("Carlos Alcaraz\xa0") == "carlos alcaraz"


def test_every_upcoming_match_gets_a_graded_prediction():
    rows = _rows()
    fs = tennis.build(ODDS, rows, Params(k=150.0, surface_weight=0.3), NOW, "a read, not a bet")
    assert len(fs) == 1  # the past match is skipped
    f = fs[0]
    assert f.winner == "home" and f.home == "Ana Strong" and f.p_home > 0.7  # the player who keeps winning
    assert (f.home_score, f.away_score) == (2.0, 0.0) and f.sport == "tennis_atp"
    assert any(x.startswith("Vegas (Bovada)") for x in f.facts) and "Test Open · R32 · best of 3." in f.facts
    L = Ledger()
    L.forecasts = {f.id: f}
    done = dict(rows[-1], status="STATUS_FINAL", winner="2", sets1="4 3", sets2="6 6")  # p2 = Bo Weak won
    assert tennis.grade(L.forecasts, rows[:-1] + [done], NOW + timedelta(hours=9)) == 1
    g = L.forecasts[f.id]
    assert g.status == "final" and g.correct is False and (g.final_away, g.final_home) == (2, 0)


def test_tennis_blind_test_runs_on_odds_history(tmp_path):
    path = tmp_path / "h.csv.gz"
    fields = ["tour", "season", "date", "surface", "bo", "winner", "loser", "comment", "psw", "psl", "b365w", "b365l",
              "maxw", "maxl", "avgw", "avgl"]
    with gzip.open(path, "wt", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for season in range(2012, 2024):
            for i in range(40):
                w.writerow({"tour": "atp", "season": season, "date": f"{season}-03-{i % 28 + 1:02d}", "surface": "Hard",
                            "bo": 3, "winner": "Strong A." if i % 4 else "Weak B.", "loser": "Weak B." if i % 4 else "Strong A.",
                            "comment": "Completed", "psw": 1.4 if i % 4 else 3.0, "psl": 3.0 if i % 4 else 1.4,
                            "b365w": 1.38, "b365l": 2.9, "maxw": 1.45, "maxl": 3.2, "avgw": 1.4, "avgl": 2.9})
    res = tennishist.run(tennishist.load(path), grid={"k": (150.0,), "surface_weight": (0.3,)}, random_n=4)
    assert res["matches"] == 480 and res["rows"] and "model" in res["log_loss_blind"]
