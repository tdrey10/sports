"""Team strength ratings fitted from game results.

Each team gets an offensive and a defensive rating. A team's expected score is

    identity link (football, basketball):  mu + hfa*home + off[team] - def[opp]
    log link (baseball, hockey, soccer):   exp(mu + hfa*home + off[team] - def[opp])

The ratings are fitted by ridge-penalised weighted least squares (identity) or
ridge-penalised Poisson regression (log). Recent games count more through an
exponential time decay, and the ridge penalty pulls teams with few games
toward average.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable

import numpy as np


@dataclass(frozen=True)
class GameResult:
    event_id: str
    date: datetime
    home: str
    away: str
    home_score: float | None
    away_score: float | None
    neutral: bool = False
    state: str = ""  # "pre", "in" or "post" when known
    detail: str = ""  # e.g. "1:28 - 3rd" for games in progress
    live_home: float | None = None
    live_away: float | None = None

    @property
    def completed(self) -> bool:
        return self.home_score is not None and self.away_score is not None


B2B_HOURS = 36.0


def rest_days(games: Iterable[GameResult]) -> dict[tuple[str, str], float]:
    """Days since each team's previous game, keyed by (event_id, team)."""
    last: dict[str, datetime] = {}
    out: dict[tuple[str, str], float] = {}
    for g in sorted(games, key=lambda g: g.date):
        for team in (g.home, g.away):
            prev = last.get(team)
            out[(g.event_id, team)] = (g.date - prev).total_seconds() / 86400.0 if prev else 7.0
            last[team] = g.date
    return out


def is_b2b(days: float | None) -> bool:
    return days is not None and days * 24 <= B2B_HOURS


@dataclass
class TeamRatings:
    link: str
    mu: float
    hfa: float
    off: dict[str, float]
    deff: dict[str, float]
    games_played: dict[str, float] = field(default_factory=dict)
    unknown_percentile: float = 50.0
    b2b_off: float = 0.0  # change to a team's own score when on a back-to-back
    b2b_def: float = 0.0  # change to a team's score when its opponent is on a back-to-back

    def _rating(self, table: dict[str, float], team: str) -> float:
        if team in table:
            return table[team]
        if not table:
            return 0.0
        return float(np.percentile(list(table.values()), self.unknown_percentile))

    def knows(self, team: str) -> bool:
        return team in self.off

    def expected_scores(
        self, home: str, away: str, neutral: bool = False, home_b2b: bool = False, away_b2b: bool = False
    ) -> tuple[float, float]:
        h = 0.0 if neutral else self.hfa
        eta_home = self.mu + h + self._rating(self.off, home) - self._rating(self.deff, away)
        eta_away = self.mu + self._rating(self.off, away) - self._rating(self.deff, home)
        eta_home += self.b2b_off * home_b2b + self.b2b_def * away_b2b
        eta_away += self.b2b_off * away_b2b + self.b2b_def * home_b2b
        if self.link == "log":
            return math.exp(eta_home), math.exp(eta_away)
        return max(eta_home, 0.1), max(eta_away, 0.1)

    def power_ranking(self) -> list[tuple[str, float, float, float]]:
        """(team, net, off, def) sorted best first; net in score units."""
        rows = []
        for team in self.off:
            o, d = self.off[team], self.deff[team]
            if self.link == "log":
                base = math.exp(self.mu)
                net = base * (math.exp(o) - math.exp(-d))
            else:
                net = o + d
            rows.append((team, net, o, d))
        rows.sort(key=lambda r: r[1], reverse=True)
        return rows


def fit_ratings(
    results: Iterable[GameResult],
    as_of: datetime,
    half_life_days: float,
    ridge: float,
    link: str = "identity",
    unknown_percentile: float = 50.0,
    max_iter: int = 50,
    rest_effects: bool = False,
) -> TeamRatings:
    """Fit ratings. With ``rest_effects`` two extra coefficients learn how much
    a back-to-back changes a team's own scoring and what it concedes."""
    games = [g for g in results if g.completed and g.date < as_of]
    if not games:
        raise ValueError("no completed games to fit ratings on")
    teams = sorted({g.home for g in games} | {g.away for g in games})
    idx = {t: i for i, t in enumerate(teams)}
    n = len(teams)
    extra = 2 if rest_effects else 0
    n_params = 2 + extra + 2 * n  # mu, hfa, [b2b_off, b2b_def], off..., def...
    base = 2 + extra
    rest = rest_days(results) if rest_effects else {}

    rows, ys, ws = [], [], []
    games_played: dict[str, float] = {t: 0.0 for t in teams}
    for g in games:
        age = (as_of - g.date).total_seconds() / 86400.0
        w = 0.5 ** (age / half_life_days)
        home_flag = 0.0 if g.neutral else 1.0
        for team, opp, score, flag in (
            (g.home, g.away, g.home_score, home_flag),
            (g.away, g.home, g.away_score, 0.0),
        ):
            own = is_b2b(rest.get((g.event_id, team)))
            other = is_b2b(rest.get((g.event_id, opp)))
            rows.append((flag, idx[team], idx[opp], own, other))
            ys.append(float(score))
            ws.append(w)
        games_played[g.home] += w
        games_played[g.away] += w

    m = len(rows)
    X = np.zeros((m, n_params))
    for r, (flag, ti, oi, own, other) in enumerate(rows):
        X[r, 0] = 1.0
        X[r, 1] = flag
        if rest_effects:
            X[r, 2] = float(own)
            X[r, 3] = float(other)
        X[r, base + ti] = 1.0
        X[r, base + n + oi] = -1.0
    y = np.asarray(ys)
    w = np.asarray(ws)
    penalty = np.full(n_params, ridge)
    penalty[:2] = 1e-6
    if rest_effects:
        penalty[2:4] = 1.0  # mild shrinkage: few back-to-backs early in a season

    if link == "identity":
        beta = _weighted_ridge(X, y, w, penalty)
    elif link == "log":
        beta = np.zeros(n_params)
        beta[0] = math.log(max(np.average(y, weights=w), 1e-3))
        for _ in range(max_iter):
            eta = X @ beta
            lam = np.exp(eta)
            z = eta + (y - lam) / lam
            # Scale the penalty by the average mean so it is comparable to
            # the identity-link penalty measured in "games".
            new = _weighted_ridge(X, z, w * lam, penalty * float(np.average(lam, weights=w)))
            if np.max(np.abs(new - beta)) < 1e-8:
                beta = new
                break
            beta = new
    else:
        raise ValueError(f"unknown link {link!r}")

    off = {t: float(beta[base + i]) for t, i in idx.items()}
    deff = {t: float(beta[base + n + i]) for t, i in idx.items()}
    return TeamRatings(
        link=link,
        mu=float(beta[0]),
        hfa=float(beta[1]),
        off=off,
        deff=deff,
        games_played=games_played,
        unknown_percentile=unknown_percentile,
        b2b_off=float(beta[2]) if rest_effects else 0.0,
        b2b_def=float(beta[3]) if rest_effects else 0.0,
    )


def _weighted_ridge(X: np.ndarray, y: np.ndarray, w: np.ndarray, penalty: np.ndarray) -> np.ndarray:
    Xw = X * w[:, None]
    A = X.T @ Xw + np.diag(penalty)
    b = Xw.T @ y
    return np.linalg.solve(A, b)
