"""Turn team ratings into outcome distributions for a single game."""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..distributions import (
    Pmf,
    apply_shape,
    discrete_normal_pmf,
    line_probs,
    negate,
    negbin_pmf_list,
    normalise,
)
from ..sports import SportConfig
from .ratings import TeamRatings


@dataclass
class GamePrediction:
    home: str
    away: str
    exp_home: float
    exp_away: float
    margin_pmf: Pmf  # home score - away score
    total_pmf: Pmf
    p_home: float  # moneyline win probs (incl. OT/extra innings for non-draw sports)
    p_away: float
    p_draw: float  # > 0 only for 3-way (soccer) markets

    @property
    def exp_margin(self) -> float:
        return self.exp_home - self.exp_away

    @property
    def exp_total(self) -> float:
        return self.exp_home + self.exp_away

    def spread(self, side: str, point: float) -> tuple[float, float, float]:
        """(win, push, loss) for ``side`` ("home"/"away") getting ``point``."""
        pmf = self.margin_pmf if side == "home" else negate(self.margin_pmf)
        return line_probs(pmf, point)

    def total(self, side: str, point: float) -> tuple[float, float, float]:
        if side == "over":
            return line_probs(self.total_pmf, -point)
        return line_probs(negate(self.total_pmf), point)

    def moneyline(self, side: str) -> tuple[float, float, float]:
        p = {"home": self.p_home, "away": self.p_away, "draw": self.p_draw}[side]
        return p, 0.0, 1.0 - p


def predict_game(
    cfg: SportConfig,
    ratings: TeamRatings,
    home: str,
    away: str,
    neutral: bool = False,
    home_b2b: bool = False,
    away_b2b: bool = False,
    home_factor: float = 1.0,
    away_factor: float = 1.0,
) -> GamePrediction:
    """``home_factor``/``away_factor`` scale each side's expected score (e.g.
    for the opposing starting pitcher)."""
    eh, ea = ratings.expected_scores(home, away, neutral, home_b2b, away_b2b)
    eh, ea = eh * home_factor, ea * away_factor
    if cfg.scoring == "normal":
        return _predict_normal(cfg, home, away, eh, ea)
    return _predict_count(cfg, home, away, eh, ea)


def _predict_normal(cfg: SportConfig, home: str, away: str, eh: float, ea: float) -> GamePrediction:
    margin = apply_shape(discrete_normal_pmf(eh - ea, cfg.margin_sd), cfg.margin_shape, absolute=True)
    total = apply_shape(discrete_normal_pmf(eh + ea, cfg.total_sd, lo=0), cfg.total_shape)
    p_win = sum(p for k, p in margin.items() if k > 0)
    p_tie = margin.get(0, 0.0)
    # Regulation ties go to overtime, treated as a coin flip.
    p_home = p_win + 0.5 * p_tie
    return GamePrediction(home, away, eh, ea, margin, total, p_home, 1.0 - p_home, 0.0)


def _predict_count(cfg: SportConfig, home: str, away: str, eh: float, ea: float) -> GamePrediction:
    n = cfg.max_score
    disp = cfg.dispersion if cfg.dispersion is not None else 1e9
    ph = negbin_pmf_list(eh, disp, n)
    pa = negbin_pmf_list(ea, disp, n)
    matrix = [[ph[i] * pa[j] for j in range(n + 1)] for i in range(n + 1)]

    rho = cfg.dixon_coles_rho
    if rho:
        # Dixon-Coles low-score correction (more 0-0 / 1-1 draws in soccer).
        matrix[0][0] *= max(1 - eh * ea * rho, 0.0)
        matrix[0][1] *= max(1 + eh * rho, 0.0)
        matrix[1][0] *= max(1 + ea * rho, 0.0)
        matrix[1][1] *= max(1 - rho, 0.0)

    margin: Pmf = {}
    total: Pmf = {}
    for i in range(n + 1):
        for j in range(n + 1):
            p = matrix[i][j]
            margin[i - j] = margin.get(i - j, 0.0) + p
            total[i + j] = total.get(i + j, 0.0) + p
    margin, total = normalise(margin), normalise(total)

    p_win = sum(p for k, p in margin.items() if k > 0)
    p_tie = margin.get(0, 0.0)
    if cfg.draws:
        return GamePrediction(home, away, eh, ea, margin, total, p_win, 1 - p_win - p_tie, p_tie)
    # Overtime / extra innings: split the tie by relative scoring strength.
    share = eh / (eh + ea) if eh + ea > 0 else 0.5
    share = 0.5 + (share - 0.5) * 0.5  # OT/shootouts are closer to coin flips
    p_home = p_win + p_tie * share
    return GamePrediction(home, away, eh, ea, margin, total, p_home, 1.0 - p_home, 0.0)


def baseline_team_score(ratings: TeamRatings, team: str) -> float:
    """A team's expected score vs. an average opponent on a neutral field."""
    off = ratings.off.get(team, 0.0)
    eta = ratings.mu + off
    return math.exp(eta) if ratings.link == "log" else max(eta, 0.1)


LEAGUE_ERA = 4.20
STARTER_SHARE = 0.6  # starters cover roughly 5.5 of 9 innings
ERA_TRUST_MAX = 0.5  # even a full season's ERA only moves halfway from league average
ERA_PRIOR_DECISIONS = 6  # decisions (W+L) at which the ERA gets half of that trust


def pitcher_factor(era: float | None, decisions: int | None = None, league_era: float = LEAGUE_ERA) -> float:
    """Multiplier on the *opposing* team's expected runs for a starting pitcher.

    ESPN lists a season ERA, which is meaningless after one start (0-0, 6.00),
    so it is trusted in proportion to the pitcher's decisions so far.
    ``decisions=None`` means unknown and uses full trust.
    """
    if era is None or era < 0:
        return 1.0
    trust = ERA_TRUST_MAX
    if decisions is not None:
        trust *= decisions / (decisions + ERA_PRIOR_DECISIONS)
    if trust <= 0:
        return 1.0
    shrunk = league_era + trust * (era - league_era)
    factor = 1 + STARTER_SHARE * (shrunk / league_era - 1)
    return min(max(factor, 0.85), 1.2)
