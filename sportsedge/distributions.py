"""Discrete outcome distributions used to price bets.

Every model produces a probability mass function (pmf) over integers: the
home margin and the game total for games, or the stat value for player props.
Pricing then works the same way for every sport and market.
"""

from __future__ import annotations

import math
from typing import Mapping

Pmf = dict[int, float]


def normal_cdf(x: float, mu: float = 0.0, sd: float = 1.0) -> float:
    return 0.5 * (1.0 + math.erf((x - mu) / (sd * math.sqrt(2.0))))


def discrete_normal_pmf(mu: float, sd: float, lo: int | None = None, hi: int | None = None) -> Pmf:
    """Normal distribution rounded to integers (continuity corrected)."""
    sd = max(sd, 1e-6)
    if lo is None:
        lo = math.floor(mu - 6 * sd) - 1
    if hi is None:
        hi = math.ceil(mu + 6 * sd) + 1
    pmf: Pmf = {}
    for k in range(lo, hi + 1):
        lower = -math.inf if k == lo else k - 0.5
        upper = math.inf if k == hi else k + 0.5
        p = normal_cdf(upper, mu, sd) - (0.0 if lower == -math.inf else normal_cdf(lower, mu, sd))
        if p > 0:
            pmf[k] = p
    return pmf


def poisson_pmf_list(lam: float, max_k: int) -> list[float]:
    lam = max(lam, 1e-9)
    out = [math.exp(-lam)]
    for k in range(1, max_k + 1):
        out.append(out[-1] * lam / k)
    return out


def negbin_pmf_list(mean: float, dispersion: float, max_k: int) -> list[float]:
    """Negative binomial with variance mean + mean**2 / dispersion.

    Falls back to Poisson when dispersion is very large (no overdispersion).
    """
    if dispersion is None or dispersion > 1e6:
        return poisson_pmf_list(mean, max_k)
    mean = max(mean, 1e-9)
    r = dispersion
    p = r / (r + mean)
    out = [p**r]
    for k in range(1, max_k + 1):
        out.append(out[-1] * (k - 1 + r) / k * (1.0 - p))
    return out


def count_pmf(mean: float, variance: float | None = None, max_k: int | None = None) -> Pmf:
    """Poisson, or negative binomial when the variance exceeds the mean."""
    if max_k is None:
        sd = math.sqrt(max(variance or mean, mean, 1e-9))
        max_k = int(mean + 10 * sd + 10)
    if variance is not None and variance > mean * 1.05:
        dispersion = mean**2 / (variance - mean)
        probs = negbin_pmf_list(mean, dispersion, max_k)
    else:
        probs = poisson_pmf_list(mean, max_k)
    pmf = {k: p for k, p in enumerate(probs) if p > 0}
    tail = 1.0 - sum(pmf.values())
    if tail > 0:
        pmf[max_k] = pmf.get(max_k, 0.0) + tail
    return pmf


def normalise(pmf: Mapping[int, float]) -> Pmf:
    total = sum(pmf.values())
    return {k: v / total for k, v in pmf.items()}


def mean_of(pmf: Mapping[int, float]) -> float:
    return sum(k * p for k, p in pmf.items())


def line_probs(pmf: Mapping[int, float], line: float) -> tuple[float, float, float]:
    """(win, push, loss) for a bet that wins when X + line > 0.

    Spreads:  X = home margin, line = the home team's spread.
    Over:     X = total, line = -total line.  Under: X = -total, line = total.
    Quarter lines (e.g. -0.25, 2.75, Asian handicaps) are split into the two
    neighbouring half-stake bets, which keeps EV exact.
    """
    quarters = round(line * 4)
    if quarters % 2 != 0:
        a = line_probs(pmf, (quarters - 1) / 4)
        b = line_probs(pmf, (quarters + 1) / 4)
        return tuple((x + y) / 2 for x, y in zip(a, b))  # type: ignore[return-value]
    win = push = loss = 0.0
    for k, p in pmf.items():
        v = k + line
        if v > 1e-9:
            win += p
        elif v < -1e-9:
            loss += p
        else:
            push += p
    return win, push, loss


def negate(pmf: Mapping[int, float]) -> Pmf:
    return {-k: p for k, p in pmf.items()}
