"""Owner, Oct 28: a college hoops efficiency / tempo engine and NBA pace, tested blind."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

from sportsedge import effstudy, proven
from sportsedge.linestudies import G
from tests.test_ledger import make_pick

T0 = datetime(2018, 11, 10, tzinfo=timezone.utc)


def game(i, home, away, hs, as_, spread=None, total=None, season=2018):
    return G("ncaab", f"g{i}", season, T0 + timedelta(days=i), home, away, hs, as_, False, {}, {}, spread,
             {"home": -110.0, "away": -110.0}, total, {"over": -110.0, "under": -110.0}, {}, None, None, None, None,
             None, True, {}, "2", {}, None, {}, {})


def test_ratings_learn_offense_and_defense_from_the_scores_only():
    r = effstudy.Ratings("ncaab", 0.1)
    for i in range(30):  # A scores a lot and gives up a lot; B is stingy
        r.add(game(i, "A", "C", 90, 80))
        r.add(game(i, "B", "C", 60, 50))
    assert r.off["A"] > r.off["B"] and r.dfn["B"] < r.dfn["A"]
    ph, pa = r.project("A", "B", neutral=True)
    assert ph > pa


def test_edges_name_the_side_the_projection_likes_at_each_threshold():
    got = effstudy.edges("ncaab", 80, 70, -4.0, 160.0)  # model: home by 10 vs a 4-point line; total 150 vs 160
    assert ("spread", "home", "ratings: spread 6+ pts off the line") in got
    assert ("under", "under", "ratings: total 8+ pts under the line") in got
    assert not any(v.startswith("ratings: total 11+") for _, _, v in got)


def test_the_study_tests_blind_and_never_sees_the_game_it_bets():
    games = [game(i, "A", "B", 80 + (i % 3), 70, spread=-2.0, total=140.0, season=2018 + i // 400) for i in range(2000)]
    res = effstudy.study(games, "ncaab", n_random=5)
    rows = {(r["market"], r["variant"]): r for r in res["rows"]}
    row = rows[("spread", "ratings: spread 6+ pts off the line")]
    assert row["in_sample"]["bets"] > 0 and row["blind"]["bets"] > 0  # 2018-2021 tune, 2022 blind
    assert res["control"]["spots"] == 10  # 5 random spot sets, on spreads and on totals


def test_a_passed_efficiency_spot_posts_on_todays_game(monkeypatch, tmp_path):
    import json

    row = {"market": "under", "variant": "ratings: total 8+ pts under the line", "verdict": "PROVEN",
           "study": "College hoops efficiency and tempo",
           "blind": {"bets": 62, "flat_roi": 0.14, "se": 0.12}, "in_sample": {"bets": 57, "flat_roi": 0.12}}
    (tmp_path / "eff_study.json").write_text(json.dumps({"ncaab": {"rows": [row]}}))
    rt = effstudy.Ratings("ncaab", 0.1)
    rt.mu, rt.games = 70.0, {"h": 10, "a": 10}
    monkeypatch.setattr(effstudy, "current", lambda sport, root: (rt, effstudy.Pace("ncaab"), {"Home U": "h", "Away U": "a"}))
    now = datetime(2026, 11, 10, 15, tzinfo=timezone.utc)
    under = make_pick(market="totals", side="under", point=152.5, american=-110, sport="ncaab", event_id="g1")
    over = make_pick(market="totals", side="over", point=152.5, american=-110, sport="ncaab", event_id="g1")
    ctx = NS(home="Home U", away="Away U", neutral=False, injuries_unknown=[], wind_mph=None,
             event=NS(commence_time=now + timedelta(hours=8)))
    scan = NS(sport=NS(key="ncaab", name="College Basketball"), picks=[under, over], contexts={"g1": ctx})
    [c] = proven.find([scan], now, tmp_path)  # projected 140 vs 152.5: 12.5 under
    assert (c.pick.side, c.units, c.proven.split(":")[0]) == ("under", 0.5, "PROVEN")
