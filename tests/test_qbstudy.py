from datetime import date, timedelta

from sportsedge import qbstudy as Q
from sportsedge.nflhist import Game


def _g(i, home, away, hqb, aqb, season=2010, hs=24, as_=17, home_ml=-150, away_ml=130, spread=3.0):
    return Game(season=season, week=i + 1, day=date(season, 9, 1) + timedelta(days=7 * i), weekday="Sunday",
                home=home, away=away, home_score=hs, away_score=as_, neutral=False, home_rest=7, away_rest=7,
                home_ml=home_ml, away_ml=away_ml, spread=spread, home_spread_odds=-110, away_spread_odds=-110,
                total=44.0, over_odds=-110, under_odds=-110, home_qb=hqb, away_qb=aqb)


def test_new_qb_and_regular_back_use_earlier_starts_only():
    gs = [_g(0, "A", "B", "q1", "b1"), _g(1, "A", "C", "q1", "c1"), _g(2, "A", "D", "q2", "d1"),
          _g(3, "A", "E", "q1", "e1")]
    c = Q.qb_context(gs)
    assert c[0]["home"] == [Q.DEBUT] and c[1]["home"] == []  # no history yet; then the regular again
    assert c[2]["home"] == [Q.NEW, Q.DEBUT]  # the backup starts (and it is his first start)
    assert c[3]["home"] == [Q.BACK]  # the regular is back after missing one game


def test_debuts_only_counted_from_2008_and_unfinished_games_teach_nothing():
    assert Q.qb_context([_g(0, "A", "B", "q1", "b1", season=2007)])[0]["home"] == []
    g0 = _g(0, "A", "B", "q1", "b1")
    g0.home_score = g0.away_score = None  # not played yet: no history from it
    c = Q.qb_context([g0, _g(1, "A", "C", "q9", "c1")])
    assert Q.NEW not in c[1]["home"] and Q.DEBUT in c[1]["home"]


def test_side_bets_spread_sign_and_price_bands():
    g = _g(0, "A", "B", "q", "b", hs=24, as_=20, home_ml=-160, away_ml=140, spread=3.0)
    home = dict((k, (p, r)) for k, p, r in Q.side_bets(g, "home"))
    away = dict((k, (p, r)) for k, p, r in Q.side_bets(g, "away"))
    assert home["lock-price fav ML"] == (-160, 1.0) and away["dog ML"] == (140, 0.0)
    assert home["spread"][1] == 1.0 and away["spread"][1] == 0.0  # home won by 4, laid 3: covers
    g.home_score = 23  # won by exactly 3: push
    assert dict((k, r) for k, _, r in Q.side_bets(g, "home"))["spread"] == 0.5
    big = _g(0, "A", "B", "q", "b", home_ml=-300, away_ml=250)
    assert [k for k, _, _ in Q.side_bets(big, "away")] == ["spread"]  # +250 is outside the dog band


def test_run_on_empty_folder_and_tiny_archive(tmp_path):
    assert Q.run(tmp_path) == {"rows": [], "control": {"spots": 0, "proven": 0, "strong": 0}}
    recs, control = {}, {}
    gs = [_g(i, "A", "B", "q1" if i != 3 else "q2", "b1", season=2010 + i // 3) for i in range(6)]
    Q.study(gs, recs, control, n_random=5)
    assert any("own new QB" in k for k in recs)
    assert all(set(v) == {"in_sample", "blind"} for v in recs.values())
