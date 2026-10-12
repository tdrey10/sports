from datetime import timedelta

from sportsedge import forecasts
from sportsedge.data.sample import SampleProvider
from sportsedge.edge import Settings
from sportsedge.engine import Engine
from sportsedge.ledger import Ledger
from sportsedge.models.ratings import GameResult
from sportsedge.sports import resolve


def _scans():
    provider = SampleProvider(seed=5)
    engine = Engine(provider, Settings(), now=provider.now)
    return provider, [engine.scan(c) for c in resolve("nba,nhl,epl")]


def test_every_game_gets_a_forecast_whose_score_names_the_same_winner():
    provider, scans = _scans()
    fs = forecasts.build(scans, Settings(), provider.now)
    games = sum(len(s.predictions) for s in scans)
    assert fs and len(fs) == games  # every game, not just picks
    for f in fs:
        total = f.p_home + f.p_away + f.p_draw
        assert abs(total - 1) < 1e-3
        assert f.winner == max(("home", "away", "draw"), key=lambda s: {"home": f.p_home, "away": f.p_away, "draw": f.p_draw}[s])
        h, a = forecasts.whole_score(f)
        if f.winner == "home":
            assert h > a
        elif f.winner == "away":
            assert a > h
        else:
            assert h == a
    assert any(f.p_draw > 0 for f in fs if f.sport == "epl") and all(f.p_draw == 0 for f in fs if f.sport == "nba")


def test_forecasts_are_our_own_read_not_the_betting_line():
    provider, scans = _scans()
    leaning = forecasts.build(scans, Settings(market_weight=1.0), provider.now)
    ignoring = forecasts.build(scans, Settings(market_weight=0.0), provider.now)
    assert [(f.home_score, f.p_home) for f in leaning] == [(f.home_score, f.p_home) for f in ignoring]
    preds = {ev: pr for s in scans for ev, pr in s.predictions.items()}
    for f in leaning:
        pr = preds[f.event_id]
        if (pr.exp_margin > 0) == (f.winner == "home") or f.winner == "draw":
            assert abs(f.home_score + f.away_score - pr.exp_total) < 0.2  # our projected total, not the O/U
    assert any(x.startswith("Vegas:") for f in leaning for x in f.facts)  # the line shown beside ours


def test_forecasts_freeze_at_kickoff_grade_on_the_final_and_survive_save(tmp_path):
    provider, scans = _scans()
    now = provider.now
    stored = {}
    f = forecasts.build(scans, Settings(), now)[0]
    forecasts.merge(stored, [f], now)
    later = f.start + timedelta(minutes=5)
    changed = forecasts.Forecast.from_dict({**f.to_dict(), "home_score": 999.0})
    forecasts.merge(stored, [changed], later)
    assert stored[f.id].home_score == f.home_score  # frozen once the game starts
    win_home = f.winner == "home"
    game = GameResult("x", f.start, f.home, f.away, 30 if win_home else 10, 10 if win_home else 30, state="post")
    assert forecasts.grade(stored, lambda sport, s, e: [game], later + timedelta(hours=4)) == 1
    g = stored[f.id]
    assert g.status == "final" and g.correct is (f.winner in ("home", "away"))
    rec = forecasts.record(stored)
    assert rec["overall"]["right"] + rec["overall"]["wrong"] == 1 and rec["overall"]["score_error"] is not None
    L = Ledger()
    L.forecasts = stored
    L.save(tmp_path / "l.json")
    assert Ledger.load(tmp_path / "l.json").forecasts[f.id].status == "final"


def _study(w_total, w_win, w_margin=0.0):
    blind = {"winners": {"blend": 0.6, "vegas": 0.6, "ratings": 0.55}, "total_error": {"blend": 1.8, "ratings": 1.9}}
    s = {"weights": {"total": w_total, "margin": w_margin, "win": w_win}, "margin_per_logit": 1.5,
         "postseason_total_adj": 0.0, "blind": blind, "games": {"blind": 5000}, "blind_seasons": "2022-2025"}
    return {"nba": s, "nhl": s}


def test_forecasts_move_the_vegas_line_toward_our_ratings_by_the_tested_weight():
    provider, scans = _scans()
    raw = {f.id: f for f in forecasts.build(scans, Settings(), provider.now)}
    vegas = {f.id: f for f in forecasts.build(scans, Settings(), provider.now, _study(0.0, 0.0))}
    half = {f.id: f for f in forecasts.build(scans, Settings(), provider.now, _study(0.5, 0.5))}
    preds = {(s.sport.key, ev): pr for s in scans for ev, pr in s.predictions.items()}
    lines = {}
    for s in scans:
        for ev, ctx in s.contexts.items():
            pts = [q.point for q in ctx.event.quotes if q.market == "totals" and q.side == "over" and q.point is not None]
            if pts:
                lines[(s.sport.key, ev)] = forecasts.statistics.median(pts)
    checked = 0
    for fid, f in vegas.items():
        key = (f.sport, f.event_id)
        if f.sport not in ("nba", "nhl") or key not in lines or f.market_home is None:
            continue
        assert abs(f.home_score + f.away_score - lines[key]) < 0.2  # all market: the O/U itself
        mid = (lines[key] + preds[key].exp_total) / 2
        assert abs(half[fid].home_score + half[fid].away_score - mid) < 0.2
        assert abs(f.p_home - f.market_home) < 1e-3
        assert any(x.startswith("Backed by 5,000 blind-tested games") for x in f.facts)
        assert any(x.startswith("Our ratings alone:") for x in f.facts)
        checked += 1
    assert checked
    # Soccer has no history test: it borrows the tested sports' average weights.
    epl = [f for f in vegas.values() if f.sport == "epl" and f.market_home is not None]
    assert epl and all(any("No history test" in x for x in f.facts) for f in epl)
    assert raw.keys() == vegas.keys()


def test_prediction_study_trusts_vegas_when_it_predicts_better():
    import random

    from sportsedge import forecaststudy

    rng = random.Random(3)
    rows = []
    for i in range(4000):
        true_total, true_margin = rng.uniform(6, 11), rng.gauss(0, 1.5)
        hs = max(0, round((true_total + true_margin) / 2 + rng.gauss(0, 2)))
        as_ = max(0, round((true_total - true_margin) / 2 + rng.gauss(0, 2)))
        p = 1 / (1 + 2.718 ** (-true_margin / 2.4))
        ml = lambda p: -100 * p / (1 - p) if p >= 0.5 else 100 * (1 - p) / p  # noqa: E731
        rows.append({"season": 2018 + i // 500, "post": False, "hs": hs, "as": as_,
                     "m_total": true_total + rng.gauss(0, 2.5), "m_margin": true_margin + rng.gauss(0, 2),
                     "p_home": min(max(p + rng.gauss(0, 0.15), 0.05), 0.95),
                     "total": true_total, "spread": -1.5, "ml": {"home": ml(p), "away": ml(1 - p)}})
    res = forecaststudy.analyze(rows, "mlb")
    assert res["weights"]["total"] <= 0.2 and res["weights"]["win"] <= 0.2
    b = res["blind"]
    assert b["total_error"]["blend"] <= b["total_error"]["ratings"]
    assert res["margin_per_logit"] > 0
