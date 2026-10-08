"""Odds conversion, vig removal, expected value and Kelly sizing."""

from __future__ import annotations

from typing import Sequence


def american_to_decimal(american: float) -> float:
    if american == 0:
        raise ValueError("American odds cannot be 0")
    if american > 0:
        return 1.0 + american / 100.0
    return 1.0 + 100.0 / -american


def decimal_to_american(decimal: float) -> float:
    if decimal <= 1.0:
        raise ValueError("Decimal odds must be > 1")
    if decimal >= 2.0:
        return round((decimal - 1.0) * 100.0)
    return round(-100.0 / (decimal - 1.0))


def implied_prob(american: float) -> float:
    """Break-even probability of a price, vig included."""
    return 1.0 / american_to_decimal(american)


def prob_to_american(p: float) -> float:
    """Fair American price for a win probability."""
    if not 0.0 < p < 1.0:
        raise ValueError("probability must be in (0, 1)")
    return decimal_to_american(1.0 / p)


def devig(implied: Sequence[float], method: str = "power") -> list[float]:
    """Remove the bookmaker margin from a set of mutually exclusive outcomes.

    ``multiplicative`` scales every probability by the overround. ``power``
    (the default) finds k with sum(p_i ** k) == 1, which shifts more of the
    margin onto longshots and better matches the favourite/longshot bias.
    """
    total = sum(implied)
    if total <= 0:
        raise ValueError("implied probabilities must be positive")
    if method == "multiplicative" or abs(total - 1.0) < 1e-9:
        return [p / total for p in implied]
    if method != "power":
        raise ValueError(f"unknown devig method {method!r}")
    lo, hi = 0.5, 3.0
    for _ in range(60):
        k = (lo + hi) / 2.0
        s = sum(p**k for p in implied)
        if abs(s - 1.0) < 1e-10:
            break
        if s > 1.0:
            lo = k
        else:
            hi = k
    k = (lo + hi) / 2.0
    out = [p**k for p in implied]
    norm = sum(out)
    return [p / norm for p in out]


def expected_value(p_win: float, p_loss: float, decimal: float) -> float:
    """EV per unit staked. Push probability returns the stake, so it adds 0."""
    return p_win * (decimal - 1.0) - p_loss


def kelly_fraction(p_win: float, p_loss: float, decimal: float) -> float:
    """Full-Kelly bankroll fraction for a bet that can push.

    Maximises p_win*ln(1+b*f) + p_loss*ln(1-f) with b = decimal - 1.
    """
    b = decimal - 1.0
    if b <= 0 or p_win + p_loss <= 0:
        return 0.0
    f = (b * p_win - p_loss) / (b * (p_win + p_loss))
    return max(0.0, f)
