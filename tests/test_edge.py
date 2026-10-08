from datetime import datetime, timezone

import pytest

from sportsedge.data.base import Event, NameMatcher, Quote
from sportsedge.edge import Settings, best_per_event, consensus_no_vig, evaluate, qualifying
from sportsedge.odds import american_to_decimal

EVENT = Event("e1", "nba", datetime(2026, 1, 1, tzinfo=timezone.utc), "Home Team", "Away Team")


def test_consensus_prefers_sharp_book():
    quotes = [
        Quote("h2h", "home", None, "pinnacle", -150),
        Quote("h2h", "away", None, "pinnacle", 140),
        Quote("h2h", "home", None, "softbook", -110),
        Quote("h2h", "away", None, "softbook", -110),
    ]
    probs = consensus_no_vig(quotes, 2, "multiplicative", ("pinnacle",))
    assert probs["home"] > 0.55
    probs = consensus_no_vig(quotes, 2, "multiplicative", ())
    assert 0.5 < probs["home"] < 0.56


def test_evaluate_uses_best_price_and_blend():
    quotes = [
        Quote("h2h", "home", None, "a", -110),
        Quote("h2h", "away", None, "a", -110),
        Quote("h2h", "home", None, "b", +105),
        Quote("h2h", "away", None, "b", -125),
    ]
    settings = Settings(market_weight=0.0, devig_method="multiplicative")
    picks = evaluate(EVENT, quotes, lambda q: (0.6, 0.0, 0.4) if q.side == "home" else (0.4, 0.0, 0.6),
                     lambda q: (q.side, ""), settings, lambda m: 2)
    home = next(p for p in picks if p.side == "home")
    assert home.book == "b" and home.american == 105
    assert home.ev == pytest.approx(0.6 * (american_to_decimal(105) - 1) - 0.4)
    assert home.kelly <= settings.max_stake

    blended = evaluate(EVENT, quotes, lambda q: (0.6, 0.0, 0.4) if q.side == "home" else (0.4, 0.0, 0.6),
                       lambda q: (q.side, ""), Settings(market_weight=1.0), lambda m: 2)
    home_b = next(p for p in blended if p.side == "home")
    assert home_b.win_prob == pytest.approx(home_b.market_prob)


def test_spread_sides_group_together():
    quotes = [
        Quote("spreads", "home", -3.5, "a", -110),
        Quote("spreads", "away", 3.5, "a", -110),
    ]
    picks = evaluate(EVENT, quotes, lambda q: (0.5, 0.0, 0.5), lambda q: (q.side, ""), Settings(), lambda m: 2)
    assert all(p.market_prob == pytest.approx(0.5) for p in picks)


def test_filters():
    quotes = [Quote("h2h", "home", None, "a", 100), Quote("h2h", "away", None, "a", -120)]
    picks = evaluate(EVENT, quotes, lambda q: (0.9, 0, 0.1) if q.side == "home" else (0.1, 0, 0.9),
                     lambda q: (q.side, ""), Settings(market_weight=0), lambda m: 2)
    s = Settings(max_ev=0.3)
    assert qualifying(picks, s) == []  # 80% EV is over the sanity cap (and flagged)
    s = Settings(max_ev=1.0, hide_flagged=False)
    assert [p.side for p in best_per_event(picks, s)] == ["home"]


def test_name_matcher():
    m = NameMatcher(["Brighton & Hove Albion", "Los Angeles Lakers", "Miami (OH) RedHawks"])
    assert m.match("Brighton and Hove Albion") == "Brighton & Hove Albion"
    assert m.match("Los Angeles Lakers") == "Los Angeles Lakers"
    assert m.match("Miami (OH) Redhawks") == "Miami (OH) RedHawks"
    assert m.match("Boston Celtics") is None
