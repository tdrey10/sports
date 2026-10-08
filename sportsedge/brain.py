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

    def to_dict(self) -> dict:
        return asdict(self)


def train(
    cfg: SportConfig, results: list[GameResult], now: datetime, test_days: int = 60
) -> tuple[SportConfig, BrainReport]:
    start = now - timedelta(days=test_days)
    rep: BacktestReport = backtest(cfg, results, start, now)
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
    )
    return tuned, report
