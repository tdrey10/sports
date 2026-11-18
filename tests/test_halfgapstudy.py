"""First half vs full game gap study (Nov 18): the fit and cutoffs use tuning seasons only, no scores leak."""

from sportsedge import halfgapstudy as H
from tests.test_dogstudy import _g


def _game(i, ml, h1_ml, season=2020, h1=(1, 0)):
    g = _g(i, "A", "B", 1, 0)
    g.sport, g.season, g.neutral = "nba", season, False
    g.ml, g.h1_ml = {"home": ml[0], "away": ml[1]}, {"home": h1_ml[0], "away": h1_ml[1]}
    g.h1 = {"home": h1[0], "away": h1[1]}
    return g


def test_fit_and_cutoffs_never_see_blind_seasons():
    tune = [_game(i, (-150, 130), (-130, 110)) for i in range(120)]
    blind = [_game(200 + i, (-150, 130), (-400, 300), season=2023) for i in range(120)]
    games = tune + blind
    xy = H.pairs(games)
    a, b = H.fit(games, xy) or (None, None)
    # The tuning seasons all sit on one point, so the fit can't be made (no spread in x): None, not a blind leak.
    assert a is None
    tune = [_game(i, (-110 - 5 * (i % 20), 100 + 4 * (i % 20)), (-105 - 3 * (i % 20), -105 + 2 * (i % 20)))
            for i in range(120)]
    games = tune + blind
    xy = H.pairs(games)
    ab = H.fit(games, xy)
    assert ab is not None
    gs = H.gaps(games, xy, ab)
    lo, hi = H.cutoffs(games, gs)
    assert hi < 0.5  # the blind games' huge first-half gaps never set the cutoffs
    assert max(gs[120:]) > 1.0


def test_spot_reads_each_sides_view_and_half_result_pushes_a_tie():
    cut = (-0.2, 0.2)
    assert H.spot(0.5, "home", cut) == "half market likes it more"
    assert H.spot(0.5, "away", cut) == "half market likes it less"
    assert H.spot(0.0, "home", cut) is None and H.spot(None, "home", cut) is None
    g = _game(0, (-150, 130), (-130, 110), h1=(10, 10))
    assert H.half_result(g, "home") == 0.5
    g = _game(0, (-150, 130), (-130, 110), h1=(7, 10))
    assert H.half_result(g, "away") == 1.0 and H.half_result(g, "home") == 0.0
    assert H.pairs([_game(0, (-150, 130), (None, None))]) == [None]
