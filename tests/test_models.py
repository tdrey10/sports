import random
from datetime import datetime, timedelta, timezone

import pytest

from sportsedge.models.game import predict_game
from sportsedge.models.props import PlayerGame, project_prop
from sportsedge.models.ratings import GameResult, fit_ratings
from sportsedge.sports import SPORTS

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _league(link, n_teams=10, n_games=600, seed=1):
    rng = random.Random(seed)
    teams = [f"T{i}" for i in range(n_teams)]
    strength = {t: (i - n_teams / 2) for i, t in enumerate(teams)}
    games = []
    for k in range(n_games):
        h, a = rng.sample(teams, 2)
        if link == "identity":
            hs = 100 + 2 + strength[h] - (-strength[a]) * 0 + rng.gauss(0, 5)
            as_ = 100 + strength[a] + rng.gauss(0, 5)
        else:
            hs = rng.choice([0, 1, 2, 3]) + (1 if strength[h] > 0 else 0)
            as_ = rng.choice([0, 1, 2, 3]) + (1 if strength[a] > 0 else 0)
        games.append(GameResult(f"g{k}", NOW - timedelta(days=200 - k / 3), h, a, hs, as_))
    return teams, strength, games


def test_identity_ratings_recover_order_and_hfa():
    teams, strength, games = _league("identity")
    r = fit_ratings(games, NOW, half_life_days=1e6, ridge=0.5)
    assert r.hfa == pytest.approx(2.0, abs=0.8)
    ranked = [t for t, *_ in r.power_ranking()]
    assert ranked[0] == "T9" and ranked[-1] == "T0"


def test_log_ratings_fit():
    teams, strength, games = _league("log")
    r = fit_ratings(games, NOW, half_life_days=1e6, ridge=1.0, link="log")
    eh, ea = r.expected_scores("T9", "T0", neutral=True)
    assert eh > ea


def test_unknown_team_gets_low_percentile():
    teams, strength, games = _league("identity")
    r = fit_ratings(games, NOW, half_life_days=1e6, ridge=0.5, unknown_percentile=10)
    eh, _ = r.expected_scores("Nobody", "T5", neutral=True)
    eh5, _ = r.expected_scores("T5", "T5", neutral=True)
    assert eh < eh5


@pytest.mark.parametrize("key", ["nfl", "nba", "mlb", "nhl", "epl"])
def test_game_prediction_consistency(key):
    cfg = SPORTS[key]
    link = cfg.link
    teams, strength, games = _league(link)
    r = fit_ratings(games, NOW, cfg.half_life_days, 1.0, link)
    pred = predict_game(cfg, r, "T8", "T1")
    assert pred.p_home + pred.p_away + pred.p_draw == pytest.approx(1.0)
    assert pred.p_home > pred.p_away
    assert sum(pred.margin_pmf.values()) == pytest.approx(1.0)
    # Over and under at the same half line are complements.
    line = round(pred.exp_total) + 0.5
    over, under = pred.total("over", line), pred.total("under", line)
    assert over[0] == pytest.approx(under[2]) and over[1] == 0
    # Home -x and away +x mirror each other.
    h = pred.spread("home", -1.5)
    a = pred.spread("away", 1.5)
    assert h[0] == pytest.approx(a[2])
    if cfg.draws:
        assert pred.p_draw > 0.15


def test_prop_projection():
    spec = SPORTS["nba"].props["player_rebounds"]
    games = [
        PlayerGame(f"g{i}", NOW - timedelta(days=i), "A Player", "T", {"REB": float(v)})
        for i, v in enumerate([10, 8, 12, 9, 11, 10, 7, 13])
    ]
    proj = project_prop(spec, "player_rebounds", "A Player", games)
    assert proj is not None
    assert 9 < proj.mean < 11
    over, push, under = proj.probs("over", 9.5)
    assert push == 0 and over > 0.5
    assert project_prop(spec, "player_rebounds", "A Player", games[:3]) is None


def test_combo_prop_requires_all_terms():
    spec = SPORTS["nba"].props["player_points_rebounds_assists"]
    games = [PlayerGame(f"g{i}", NOW, "P", "T", {"PTS": 20.0, "REB": 5.0}) for i in range(6)]
    assert project_prop(spec, "m", "P", games) is None
