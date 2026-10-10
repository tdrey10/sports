import csv
from datetime import date, timedelta

from sportsedge import nflhist
from sportsedge.nflhist import Game, Params


def _game(i, season, week, home, away, hs, as_, **kw):
    base = dict(season=season, week=week, day=date(season, 9, 7) + timedelta(days=7 * (week - 1)) + timedelta(hours=i),
                weekday="Sunday", home=home, away=away, home_score=hs, away_score=as_, neutral=False,
                home_rest=7, away_rest=7, home_ml=-150, away_ml=130, spread=3.0, home_spread_odds=-110,
                away_spread_odds=-110, total=44.0, over_odds=-110, under_odds=-110, home_qb="q" + home,
                away_qb="q" + away)
    base.update(kw)
    return Game(**base)


def test_save_keeps_one_file_per_season_from_2006(tmp_path):
    rows = [{"season": "2005", "game_id": "old"}, {"season": "2006", "game_id": "a"}, {"season": "2024", "game_id": "b"}]
    assert nflhist.save(tmp_path, rows) == 2
    assert sorted(p.name for p in tmp_path.glob("*.csv")) == ["2006.csv", "2024.csv"]
    assert list(csv.DictReader(open(tmp_path / "2024.csv")))[0]["game_id"] == "b"


def test_ratings_predict_only_from_earlier_games():
    games = [_game(0, 2010, 1, "A", "B", 30, 0), _game(0, 2010, 2, "A", "B", 30, 0)]
    preds = nflhist.replay(games, Params(k=0.1, hfa=0, rest=0, qb=0, carry=1))
    assert preds[0].margin == 0  # nothing known before the first game
    assert preds[1].margin > 0  # A learned from game one


def test_new_starting_quarterback_costs_points():
    games = [_game(w, 2010, w, "A", "B", 20, 20, home_qb="star") for w in range(1, 5)]
    games.append(_game(9, 2010, 5, "A", "B", 20, 20, home_qb="backup"))
    with_qb = nflhist.replay(games, Params(k=0, hfa=0, rest=0, qb=3, carry=1))
    assert with_qb[-1].home_qb_new and with_qb[-1].margin == -3


def test_bye_week_and_blowout_spots():
    games = [_game(0, 2010, 1, "A", "B", 35, 7), _game(0, 2010, 1, "C", "D", 10, 7),
             _game(0, 2010, 3, "A", "C", 10, 7, home_ml=-200, away_ml=170)]  # A off a bye; C off a bye too
    preds = nflhist.replay(games, Params())
    assert ("blew someone out last week (21+)", "home") in nflhist.spots(preds[2])
    assert not any(s.startswith("dog off a bye") for s, _ in nflhist.spots(preds[2]))  # both rested


def test_bets_need_the_edge_and_never_lay_more_than_150():
    pred = nflhist.Pred(_game(0, 2020, 1, "A", "B", 24, 10), 0, False, False)
    row = nflhist.Row(pred, "ml", {"home": 0.80, "away": 0.20}, {"home": 0.68, "away": 0.32},
                      {"home": -230, "away": 190}, {"home": 1.0, "away": 0.0})
    assert nflhist.bet([row], trust=1.0).bets == 0  # home has the edge but costs -230
    row.price = {"home": -140, "away": 120}
    rec = nflhist.bet([row], trust=1.0)
    assert rec.bets == 1 and rec.won == 1 and rec.units > 0
    assert nflhist.bet([row], trust=0.0).bets == 0  # full trust in the market: no edge


def test_record_roi_and_proven_rule():
    rec = nflhist.Record()
    for i in range(60):
        rec.add(-110, 1.0 if i % 2 else 0.0)
    assert rec.bets == 60 and abs(rec.flat_roi - (-0.0455)) < 0.001 and not rec.proven
    assert nflhist.profit(150, 1.0) == 1.5 and nflhist.profit(-110, 0.5) == 0.0


def test_wind_unders_split_in_sample_and_blind():
    games = [_game(0, 2010, 1, "A", "B", 10, 13, wind=20), _game(0, 2020, 1, "A", "B", 30, 30, wind=18),
             _game(1, 2020, 1, "C", "D", 3, 3, wind=25, outdoors=False), _game(2, 2020, 1, "E", "F", 3, 3, wind=5)]
    rec = nflhist.wind_unders(games, blind_from=2018)
    assert (rec["in_sample"].won, rec["blind"].lost, rec["blind"].bets) == (1, 1, 1)


def test_full_run_reports_blind_and_in_sample():
    teams = "ABCDEFGH"
    games = []
    for season in range(2006, 2021):
        for week in range(1, 9):
            for i in range(4):
                h, a = teams[(i + week) % 8], teams[(i + week + 4) % 8]
                games.append(_game(i, season, week, h, a, 20 + (ord(h) % 5), 17 + (ord(a) % 4)))
    grid = {"k": (0.08,), "hfa": (1.5,), "rest": (0.0,), "qb": (0.0,), "carry": (0.7,)}
    res = nflhist.run(games, tune_last=2015, grid=grid)
    assert res["blind_seasons"] == "2016-2020"
    assert set(res["markets"]) == {"ml", "spread"}
    text = nflhist.report(res)
    assert "BLIND" in text and "in-sample" in text


def test_regular_quarterback_tie_goes_to_the_most_recent_starter():
    # A set's order changes from run to run; a tie must not, or results drift between runs.
    assert nflhist._regular_qb(["a", "b", "a", "b"]) == "b"
    assert nflhist._regular_qb(["b", "a", "b", "a"]) == "a"


def test_long_shots_are_never_bet_even_with_an_edge():
    pred = nflhist.Pred(_game(0, 2020, 1, "A", "B", 10, 24), 0, False, False)
    row = nflhist.Row(pred, "ml", {"home": 0.75, "away": 0.25}, {"home": 0.88, "away": 0.12},
                      {"home": -800, "away": 650}, {"home": 0.0, "away": 1.0})
    assert nflhist.bet([row], trust=1.0).bets == 0  # +650 dog with a 13-point edge: still no bet


def test_proven_needs_both_periods():
    good = {"bets": 100, "won": 60, "lost": 40, "push": 0, "units": 10, "roi": 0.1, "flat_roi": 0.1, "se": 0.05,
            "proven": True}
    assert nflhist.held_up({"in_sample": good, "blind": good}) == "  PROVEN"
    assert "blind only" in nflhist.held_up({"in_sample": {**good, "flat_roi": -0.05}, "blind": good})
