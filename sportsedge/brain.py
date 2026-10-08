"""The Brain: daily walk-forward retraining and self-calibration.

Every run, the game model is re-tested on recent games it never saw
(ratings refitted weekly using only earlier games). That gives an honest
accuracy number and, for points-based sports, re-tunes the margin/total
spread so win probabilities match how often teams actually win.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timedelta

from .backtest import BacktestReport, backtest
from .distributions import shape_factors
from .models.ratings import GameResult
from .sports import SportConfig

PRIOR_GAMES = 150  # weight of the configured sd when blending with measured error


@dataclass
class BrainReport:
    sport: str
    name: str
    trained_at: str
    games: int
    accuracy: float
    log_loss: float
    baseline_log_loss: float
    brier: float
    margin_mae: float
    total_mae: float
    margin_sd: float
    total_sd: float
    calibration: list[tuple[float, float, int]] = field(default_factory=list)
    key_numbers: list[tuple[int, float]] = field(default_factory=list)  # strongest learned margin spikes

    def to_dict(self) -> dict:
        return asdict(self)


def train(
    cfg: SportConfig, results: list[GameResult], now: datetime, test_days: int = 60
) -> tuple[SportConfig, BrainReport]:
    start = now - timedelta(days=test_days)
    if cfg.scoring == "normal":
        cfg = with_shapes(cfg, results, before=start)  # learned only from games before the test window
    rep: BacktestReport = backtest(cfg, results, start, now)
    if cfg.scoring == "normal":
        cfg = with_shapes(cfg, results, before=now)  # then refreshed with everything for live use
    tuned = cfg
    if cfg.scoring == "normal" and rep.games >= 30:
        n = rep.games
        margin_sd = math.sqrt((n * rep.margin_rmse**2 + PRIOR_GAMES * cfg.margin_sd**2) / (n + PRIOR_GAMES))
        total_sd = math.sqrt((n * rep.total_rmse**2 + PRIOR_GAMES * cfg.total_sd**2) / (n + PRIOR_GAMES))
        tuned = replace(cfg, margin_sd=margin_sd, total_sd=total_sd)
    report = BrainReport(
        sport=cfg.key,
        name=cfg.name,
        trained_at=now.isoformat(),
        games=rep.games,
        accuracy=rep.accuracy,
        log_loss=rep.log_loss,
        baseline_log_loss=rep.baseline_log_loss,
        brier=rep.brier,
        margin_mae=rep.margin_mae,
        total_mae=rep.total_mae,
        margin_sd=tuned.margin_sd,
        total_sd=tuned.total_sd,
        calibration=rep.calibration,
        key_numbers=sorted(tuned.margin_shape.items(), key=lambda kv: -kv[1])[:4],
    )
    return tuned, report


MIN_SHAPE_GAMES = 150


def with_shapes(cfg: SportConfig, results: list[GameResult], before: datetime) -> SportConfig:
    """Learn the sport's key numbers for margins and totals from final scores."""
    done = [g for g in results if g.completed and g.date < before]
    if len(done) < MIN_SHAPE_GAMES:
        return cfg
    margins = [round(g.home_score - g.away_score) for g in done]
    totals = [round(g.home_score + g.away_score) for g in done]
    return replace(
        cfg,
        margin_shape=shape_factors(margins, window=3, absolute=True),
        total_shape=shape_factors(totals, window=4),
    )
