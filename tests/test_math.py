import math

import pytest

from sportsedge.distributions import count_pmf, discrete_normal_pmf, line_probs, mean_of
from sportsedge.odds import (
    american_to_decimal,
    decimal_to_american,
    devig,
    expected_value,
    implied_prob,
    kelly_fraction,
    prob_to_american,
)


def test_american_decimal_roundtrip():
    assert american_to_decimal(-110) == pytest.approx(1.9090909)
    assert american_to_decimal(150) == pytest.approx(2.5)
    for a in (-250, -110, 100, 135, 400):
        assert decimal_to_american(american_to_decimal(a)) == a


def test_implied_and_fair():
    assert implied_prob(-110) == pytest.approx(0.5238, abs=1e-4)
    assert prob_to_american(0.5) == 100
    assert prob_to_american(0.6) == -150


@pytest.mark.parametrize("method", ["power", "multiplicative"])
def test_devig_sums_to_one(method):
    fair = devig([implied_prob(-110), implied_prob(-110)], method)
    assert fair == pytest.approx([0.5, 0.5])
    fair = devig([implied_prob(-300), implied_prob(240)], method)
    assert sum(fair) == pytest.approx(1.0)
    assert fair[0] > 0.7


def test_power_devig_shades_longshot_more():
    implied = [implied_prob(-400), implied_prob(300)]
    mult = devig(implied, "multiplicative")
    power = devig(implied, "power")
    assert power[1] < mult[1]


def test_ev_and_kelly():
    dec = american_to_decimal(100)
    assert expected_value(0.55, 0.45, dec) == pytest.approx(0.10)
    assert kelly_fraction(0.55, 0.45, dec) == pytest.approx(0.10)
    assert kelly_fraction(0.40, 0.60, dec) == 0.0
    # A push chance shrinks neither EV of the win/loss legs nor allows negative stakes.
    assert expected_value(0.5, 0.4, dec) == pytest.approx(0.10)


def test_discrete_normal_is_a_distribution():
    pmf = discrete_normal_pmf(3.0, 13.5)
    assert sum(pmf.values()) == pytest.approx(1.0)
    assert mean_of(pmf) == pytest.approx(3.0, abs=0.05)


def test_count_pmf_poisson_and_negbin():
    pois = count_pmf(2.5)
    nb = count_pmf(2.5, variance=5.0)
    assert sum(pois.values()) == pytest.approx(1.0)
    assert sum(nb.values()) == pytest.approx(1.0)
    assert mean_of(pois) == pytest.approx(2.5, abs=1e-3)
    assert mean_of(nb) == pytest.approx(2.5, abs=1e-2)
    assert nb[0] > pois[0]  # overdispersion fattens the zero


def test_line_probs_push_and_halves():
    pmf = {-1: 0.25, 0: 0.25, 3: 0.5}
    assert line_probs(pmf, -3) == pytest.approx((0.0, 0.5, 0.5))  # -3 pushes on exactly 3
    assert line_probs(pmf, -2.5) == pytest.approx((0.5, 0.0, 0.5))
    assert line_probs(pmf, 0.5) == pytest.approx((0.75, 0.0, 0.25))


def test_quarter_line_is_average_of_halves():
    pmf = {0: 0.3, 1: 0.4, 2: 0.3}
    q = line_probs(pmf, -0.75)
    a, b = line_probs(pmf, -0.5), line_probs(pmf, -1.0)
    assert q == pytest.approx(tuple((x + y) / 2 for x, y in zip(a, b)))
    assert math.isclose(sum(q), 1.0)
