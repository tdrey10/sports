"""Player prop projections from recent game logs."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime

from ..distributions import Pmf, count_pmf, discrete_gamma_pmf, line_probs, negate
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
    notes: list[str] = field(default_factory=list)  # adjustments applied, for display

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
    factor: float = 1.0,
    half_life_games: float = 6.0,
    min_games: int = 4,
    notes: list[str] | None = None,
) -> PropProjection | None:
    """Exponentially weighted projection of a player stat.

    Uses the player's most recent games (newest weighted most), scaled by
    ``factor`` (matchup, game script, teammates out). The variance
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

    factor = min(max(factor, 0.7), 1.35)
    mean *= factor
    var *= factor * factor

    # Shrink the variance toward a prior so a hot/cold streak of near-identical
    # games doesn't produce an overconfident distribution.
    prior_var = max(mean, 0.25) if spec.dist == "count" else (0.45 * max(mean, 1.0)) ** 2
    k = 4.0
    var = (n_eff * var + k * prior_var) / (n_eff + k)

    if spec.dist == "count":
        var = max(var, mean)
        pmf = count_pmf(max(mean, 0.01), var)
    else:
        pmf = discrete_gamma_pmf(mean, math.sqrt(var))
    return PropProjection(player, market, mean, math.sqrt(var), len(values), pmf, list(notes or []))


SHRINK_GAMES = 8.0  # pseudo-games of league average behind each matchup factor
USAGE_RETENTION = 0.5  # share of a missing player's production picked up by teammates
RECENT_GAMES = 3  # an injured player only counts as "newly missing" if they played this recently


def matchup_factor(spec: PropSpec, lines: list[PlayerGame], opponent: str) -> float:
    """How much of this stat ``opponent`` gives up per game vs. the average team.

    Built from box scores: in each of the opponent's games, the stat summed
    over the players facing them, compared with the per-team-game average of
    every game in the sample. Shrunk toward 1 with ``SHRINK_GAMES``.
    """
    per_team_game: dict[tuple[str, str], float] = {}
    for pg in lines:
        v = stat_value(spec, pg.stats)
        if v is None:
            continue
        key = (pg.event_id, pg.team)
        per_team_game[key] = per_team_game.get(key, 0.0) + v
    if len(per_team_game) < 6:
        return 1.0
    league = sum(per_team_game.values()) / len(per_team_game)
    if league <= 0:
        return 1.0
    opp_games = {pg.event_id for pg in lines if pg.team == opponent}
    allowed = [v for (ev, team), v in per_team_game.items() if ev in opp_games and team != opponent]
    if not allowed:
        return 1.0
    n = len(allowed)
    shrunk = (sum(allowed) + SHRINK_GAMES * league) / (n + SHRINK_GAMES)
    return min(max(shrunk / league, 0.85), 1.15)


def usage_factor(spec: PropSpec, lines: list[PlayerGame], team: str, out_players: set[str]) -> tuple[float, list[str]]:
    """Boost for a team's healthy players when recent regulars are ruled out."""
    if not out_players:
        return 1.0, []
    team_lines = [pg for pg in lines if pg.team == team]
    recent = sorted({(pg.date, pg.event_id) for pg in team_lines})[-RECENT_GAMES:]
    recent_ids = {ev for _, ev in recent}
    by_player: dict[str, list[float]] = {}
    for pg in team_lines:
        v = stat_value(spec, pg.stats)
        if v is not None:
            by_player.setdefault(pg.player, []).append(v)
    missing = [
        p for p in out_players
        if p in by_player and any(pg.player == p and pg.event_id in recent_ids for pg in team_lines)
    ]
    if not missing:
        return 1.0, []
    out_total = sum(sum(by_player[p]) / len(by_player[p]) for p in missing)
    remaining = sum(sum(v) / len(v) for p, v in by_player.items() if p not in out_players)
    if remaining <= 0:
        return 1.0, []
    factor = min(1 + USAGE_RETENTION * out_total / remaining, 1.25)
    return factor, missing
