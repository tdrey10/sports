"""Player prop projections from recent game logs."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime

from ..distributions import Pmf, count_pmf, discrete_normal_pmf, line_probs, negate
from ..sports import PropSpec


@dataclass(frozen=True)
class PlayerGame:
    event_id: str
    date: datetime
    player: str
    team: str
    stats: dict[str, float] = field(default_factory=dict)


@dataclass
class PropProjection:
    player: str
    market: str
    mean: float
    sd: float
    games: int
    pmf: Pmf

    def probs(self, side: str, point: float) -> tuple[float, float, float]:
        if side == "over":
            return line_probs(self.pmf, -point)
        return line_probs(negate(self.pmf), point)


def stat_value(spec: PropSpec, stats: dict[str, float]) -> float | None:
    total = 0.0
    for candidates in spec.terms:
        for key in candidates:
            if key in stats:
                total += stats[key]
                break
        else:
            return None
    return total


def project_prop(
    spec: PropSpec,
    market: str,
    player: str,
    games: list[PlayerGame],
    team_factor: float = 1.0,
    half_life_games: float = 6.0,
    min_games: int = 4,
) -> PropProjection | None:
    """Exponentially weighted projection of a player stat.

    Uses the player's most recent games (newest weighted most). The variance
    is floored at the mean for count stats (no underdispersion) and the sd is
    shrunk toward a sensible fraction of the mean for small samples.
    """
    games = sorted(games, key=lambda g: g.date, reverse=True)
    values: list[float] = []
    for g in games:
        v = stat_value(spec, g.stats)
        if v is not None:
            values.append(v)
    if len(values) < min_games:
        return None

    weights = [0.5 ** (i / half_life_games) for i in range(len(values))]
    wsum = sum(weights)
    mean = sum(w * v for w, v in zip(weights, values)) / wsum
    var = sum(w * (v - mean) ** 2 for w, v in zip(weights, values)) / wsum
    n_eff = wsum**2 / sum(w * w for w in weights)
    if n_eff > 1:
        var *= n_eff / (n_eff - 1)  # unbiased

    if spec.scales_with_team:
        factor = min(max(team_factor, 0.85), 1.15)
        mean *= factor
        var *= factor * factor

    # Shrink the variance toward a prior so a hot/cold streak of near-identical
    # games doesn't produce an overconfident distribution.
    prior_var = max(mean, 0.25) if spec.dist == "count" else (0.35 * max(mean, 1.0)) ** 2
    k = 4.0
    var = (n_eff * var + k * prior_var) / (n_eff + k)

    if spec.dist == "count":
        var = max(var, mean)
        pmf = count_pmf(max(mean, 0.01), var)
    else:
        pmf = discrete_normal_pmf(mean, math.sqrt(var), lo=0)
    return PropProjection(player, market, mean, math.sqrt(var), len(values), pmf)
