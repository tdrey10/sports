import json
import math
import random

from sportsedge.data.sample import SampleProvider
from sportsedge.edge import Settings, edge_score
from sportsedge.sports import resolve
from sportsedge.study import (
    ClosingHistory, Side, calibrate, fit_platt, logit, run_studies, select, study_sides, summarize,
)
from sportsedge.sports import SPORTS

from datetime import datetime, timezone

from sportsedge.edge import Pick


def make_pick(american=-110, win_prob=0.55, ev=0.0, sport="nba"):
    return Pick(
        sport=sport, event_id="e", commence_time=datetime(2026, 10, 1, tzinfo=timezone.utc), matchup="A @ B",
        market="spreads", market_label="Spread", selection="sel", side="home", point=-3.5, player=None,
        book="draftkings", american=american, model_prob=win_prob, market_prob=win_prob, win_prob=win_prob,
        push_prob=0.0, fair_american=-120, ev=ev, kelly=0.01, stake=None, projection="", books=1,
    )


def side(eid, model, market, american=-110, won=True, market_name="spreads"):
    return Side("nba", eid, market_name, "home", american, model, market, 0.0,
                1.0 if won else 0.0, 0.0, 0.0 if won else 1.0)


def test_select_takes_best_qualifying_side_per_game():
    sides = [side("g1", 0.62, 0.52), side("g1", 0.60, 0.50), side("g2", 0.51, 0.50), side("g3", 0.80, 0.50)]
    chosen = select(sides, w=0.5, min_ev=0.02)
    assert [s.event_id for s in chosen] == ["g1"]  # g2 has no edge, g3 is hidden (model/market gap > 12%)
    assert chosen[0].model_prob == 0.62


def test_summarize_roi_and_standard_error():
    bets = [side(f"g{i}", 0.6, 0.5, american=100, won=i % 2 == 0) for i in range(40)]
    r = summarize("x", bets)
    assert r.bets == 40 and r.won == 20 and r.lost == 20
    assert abs(r.roi) < 1e-9 and r.se > 0


def test_platt_recovers_overconfidence():
    rng = random.Random(1)
    xs, ys = [], []
    for _ in range(4000):
        said = rng.uniform(0.2, 0.8)
        true = 0.5 + (said - 0.5) * 0.5  # the model is twice as confident as it should be
        xs.append(logit(said))
        ys.append(1.0 if rng.random() < true else 0.0)
    a, b = fit_platt(xs, ys)
    assert 0.35 < b < 0.7 and abs(a) < 0.1
    assert calibrate(0.7, (a, b)) < 0.65


def test_edge_score_rewards_edge_and_likelihood():
    big_safe = make_pick(american=-110, win_prob=0.60, ev=0.0)
    small_safe = make_pick(american=-110, win_prob=0.54, ev=0.0)
    longshot = make_pick(american=400, win_prob=0.22, ev=0.0)
    s = Settings()
    assert edge_score(big_safe, s)[0] > edge_score(small_safe, s)[0]
    assert edge_score(big_safe, s)[0] > edge_score(longshot, s)[0]
    shrunk = Settings(calibration={"nba": (0.0, 0.5)})
    assert edge_score(big_safe, shrunk)[1] < 0.60  # calibration pulls an overconfident 60% in


def test_study_on_demo_history(tmp_path):
    provider = SampleProvider(seed=4)
    history = ClosingHistory(tmp_path / "history", provider.closing_quotes, budget=400)
    studies = run_studies(resolve("nba,nhl"), provider.game_results, history, provider.now, days=90)
    history.save()
    assert set(studies) == {"nba", "nhl"}
    for st in studies.values():
        assert st.games > 0 and st.sides > st.games
        assert 0.6 <= st.weight <= 0.8 and st.min_ev >= 0.02 or st.proven
        assert st.by_min_ev and st.weight_log_loss
    saved = json.loads((tmp_path / "history" / "nba.json").read_text())
    assert saved and all(isinstance(v, list) for v in saved.values())
    # A second run reuses the saved lines instead of downloading them again.
    again = ClosingHistory(tmp_path / "history", provider.closing_quotes, budget=400)
    run_studies(resolve("nba"), provider.game_results, again, provider.now, days=90)
    assert again.fetched < history.fetched


def test_unproven_sport_never_loosens_the_threshold():
    rng = random.Random(2)
    sides = [side(f"g{i}", 0.6, 0.5, american=-110, won=rng.random() < 0.45) for i in range(400)]
    st = study_sides(sides, SPORTS["nba"], games=400, window="test")
    assert not st.proven and st.min_ev >= 0.02
