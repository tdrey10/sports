import csv

from sportsedge import soccerstudy


def _write(folder, rows):
    (folder / "epl").mkdir(parents=True, exist_ok=True)
    fields = ["event_id", "date", "home", "away", "home_score", "away_score", "neutral", "close_home_ml", "close_away_ml",
              "close_draw_ml", "close_total", "close_over_price", "close_under_price"]
    with open(folder / "epl" / "2025-09.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow(r)


def test_form_and_streaks_come_from_earlier_results_only(tmp_path):
    rows = []
    for i in range(8):  # Strong beat Weak 8 times running
        rows.append({"event_id": i, "date": f"2025-09-{i + 1:02d}T15:00:00+00:00", "home": "Strong", "away": "Weak",
                     "home_score": 2, "away_score": 0, "neutral": 0, "close_home_ml": -150, "close_away_ml": 400,
                     "close_draw_ml": 280, "close_total": 2.5, "close_over_price": -110, "close_under_price": -110})
    _write(tmp_path, rows)
    games = soccerstudy.load(tmp_path)
    soccerstudy.annotate(games)
    last = games[-1]["ctx"]
    assert last["home"]["streak"] == ("W", 7) and last["away"]["winless"] == 7  # the 8th match isn't counted
    assert last["h2h_home_wins"] == 3 and last["home"]["pts5"] == 15
    res = soccerstudy.run(tmp_path, random_n=5)
    assert res["priced"] == 8 and "luck control" in soccerstudy.report(res)
