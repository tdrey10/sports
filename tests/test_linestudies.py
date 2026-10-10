from datetime import datetime, timedelta, timezone

from sportsedge import linestudies as L
from sportsedge.nflhist import Record


def _g(i, home, away, hs, as_, ml=(-150, 130), starter=("", ""), sport="nhl", season=2022):
    return L.G(sport, str(i), season, datetime(2023, 1, 1, tzinfo=timezone.utc) + timedelta(days=i), home, away,
               hs, as_, False, {"home": ml[0], "away": ml[1]}, {"home": None, "away": None}, None,
               {"home": None, "away": None}, None, {"over": None, "under": None}, {"home": 1.0, "away": 1.0},
               None, None, None, None, None, True, {"home": starter[0], "away": starter[1]}, "2",
               {"home": None, "away": None}, None, {"home": None, "away": None}, {"home": None, "away": None})


def _rec(n, roi_each):
    r = Record()
    for x in roi_each * n:
        r.add(-110 if x < 0 else 100, 1.0 if x > 0 else 0.0)
    return r


def test_annotate_streaks_last_result_and_backup_starter():
    games = [_g(i, "A", "B", 3, 1, starter=("G1", "X")) for i in range(6)]
    games.append(_g(6, "A", "B", 1, 2, starter=("G2", "X")))
    L.annotate(games)
    last = games[-1].ctx
    assert last["home"]["streak"] == 6 and last["away"]["streak"] == -6
    assert last["home"]["last_margin"] == 2 and last["home"]["backup"] is True
    assert last["away"]["backup"] is False
    assert last["away"]["starter_last"] == 3  # X allowed 3 last time


def test_verdicts_ceiling_never_counts():
    win = {"in_sample": _rec(40, [1, 1, -1]), "blind": _rec(40, [1, 1, -1])}
    assert L.verdict(win) == "STRONG"
    assert L.verdict(win, "ceiling: peeks ahead") == ""
    thin = {"in_sample": _rec(5, [1, 1, -1]), "blind": _rec(40, [1, 1, -1])}
    assert L.verdict(thin) == "PROVEN"  # tuning made money, but too few bets to be STRONG
    lose = {"in_sample": _rec(40, [-1, -1, 1]), "blind": _rec(40, [1, 1, -1])}
    assert L.verdict(lose) == ""


def test_only_strong_variants_with_a_live_spot_get_weights():
    blind = {"bets": 300, "flat_roi": 0.10, "se": 0.04}
    rows = [
        {"study": "09 rest", "variant": "rested dog vs a back-to-back", "sport": "nba", "verdict": "STRONG", "blind": blind},
        {"study": "09 rest", "variant": "rested dog vs a back-to-back", "sport": "nhl", "verdict": "PROVEN", "blind": blind},
        {"study": "11 streaks", "variant": "dog on a 3+ win streak", "sport": "nba", "verdict": "STRONG", "blind": blind},
        {"study": "06 weather totals", "variant": "under, wind 15+ mph", "sport": "nfl", "verdict": "STRONG",
         "blind": blind},
    ]
    w = L.spot_weights({"rows": rows})
    assert set(w) == {"nba:rested_dog", "nfl:wind_under"}
    assert abs(w["nba:rested_dog"] - 0.06 / L.DOG_DECIMAL) < 1e-9
    assert abs(w["nfl:wind_under"] - 0.06 / L.TOTAL_DECIMAL) < 1e-9
    assert L.spot_weights(None) == {}


def test_run_studies_records_dogs_by_band():
    games = [_g(i, "A", "B", 1 if i % 2 else 3, 2, ml=(-140, 120)) for i in range(10)]
    L.annotate(games)
    book = L.Book()
    L.run_studies(games, "nhl", book)
    rec = book.recs[("01 price band", "dog +100..+130", "nhl")]["blind"]
    assert rec.bets == 10 and rec.won == 5
