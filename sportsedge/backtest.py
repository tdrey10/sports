"""Walk-forward backtest of the game model against final scores.

Needs no odds: ratings are refitted every ``refit_days`` using only games
played before that point, then used to predict the following games. Reports
moneyline log loss / Brier score, margin and total errors, and a calibration
table so you can tune the per-sport settings before trusting any edge.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .models.game import predict_game
from .models.ratings import GameResult, fit_ratings
from .sports import SportConfig


@dataclass
class BacktestReport:
    sport: str
    games: int = 0
    log_loss: float = 0.0
    brier: float = 0.0
    accuracy: float = 0.0
    margin_mae: float = 0.0
    total_mae: float = 0.0
    baseline_log_loss: float = 0.0  # always predicting the home-win base rate
    calibration: list[tuple[float, float, int]] = field(default_factory=list)  # (pred, actual, n)


def backtest(
    cfg: SportConfig,
    results: list[GameResult],
    start: datetime,
    end: datetime,
    refit_days: int = 7,
    min_train_games: int = 50,
) -> BacktestReport:
    completed = sorted((g for g in results if g.completed), key=lambda g: g.date)
    rows: list[tuple[float, float, float, float, float, float]] = []  # p_home, outcome, pm, am, pt, at
    cursor = start
    while cursor < end:
        nxt = min(cursor + timedelta(days=refit_days), end)
        train = [g for g in completed if g.date < cursor and g.date >= cursor - timedelta(days=cfg.lookback_days)]
        test = [g for g in completed if cursor <= g.date < nxt]
        cursor = nxt
        if len(train) < min_train_games or not test:
            continue
        ratings = fit_ratings(train, cursor, cfg.half_life_days, cfg.ridge, cfg.link, cfg.unknown_percentile)
        for g in test:
            pred = predict_game(cfg, ratings, g.home, g.away, g.neutral)
            margin = g.home_score - g.away_score
            if cfg.draws:
                # Score the 3-way market as home-win vs not.
                outcome = 1.0 if margin > 0 else 0.0
                p = pred.p_home
            else:
                if margin == 0:
                    continue  # ties in non-draw sports (rare, e.g. NFL) are skipped
                outcome = 1.0 if margin > 0 else 0.0
                p = pred.p_home
            rows.append((p, outcome, pred.exp_margin, margin, pred.exp_total, g.home_score + g.away_score))

    report = BacktestReport(cfg.key, games=len(rows))
    if not rows:
        return report
    eps = 1e-6
    n = len(rows)
    report.log_loss = -sum(o * math.log(max(p, eps)) + (1 - o) * math.log(max(1 - p, eps)) for p, o, *_ in rows) / n
    report.brier = sum((p - o) ** 2 for p, o, *_ in rows) / n
    report.accuracy = sum((p > 0.5) == (o == 1.0) for p, o, *_ in rows) / n
    report.margin_mae = sum(abs(pm - am) for _, _, pm, am, _, _ in rows) / n
    report.total_mae = sum(abs(pt - at) for *_, pt, at in rows) / n
    base = sum(o for _, o, *_ in rows) / n
    base = min(max(base, eps), 1 - eps)
    report.baseline_log_loss = -(base * math.log(base) + (1 - base) * math.log(1 - base))

    buckets: dict[int, list[tuple[float, float]]] = {}
    for p, o, *_ in rows:
        buckets.setdefault(min(int(p * 10), 9), []).append((p, o))
    for b in sorted(buckets):
        items = buckets[b]
        report.calibration.append(
            (sum(p for p, _ in items) / len(items), sum(o for _, o in items) / len(items), len(items))
        )
    return report
