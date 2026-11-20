"""Postseason and neutral-site study (Nov 20), and the rule it produced: the Lock stays out of
playoff / bowl / March Madness games (lock-price favorites lost ~11% there, tuning and blind)."""

from dataclasses import replace

from sportsedge import postseasonstudy as P
from tests.test_dogstudy import _g


def _post(i, home, away, hs, as_):
    g = _g(i, home, away, hs, as_)
    g.stype = "3"
    return g


def test_series_last_uses_only_earlier_games_of_the_same_postseason_series():
    games = [_g(0, "A", "B", 1, 0), _post(1, "A", "B", 1, 0), _post(2, "B", "A", 3, 0), _post(3, "A", "B", 0, 0)]
    s = P.series_last(games)
    assert s[0] == {} and s[1] == {}  # regular-season meeting doesn't start the series
    assert s[2] == {"home": "lost", "away": "won"}  # A won game 1 of the series (B now at home)
    assert s[3] == {"home": "lost", "away": "won"}  # B won game 2; game 3's own result never leaks in


def test_spots_and_sides():
    g = _post(0, "A", "B", 1, 0)
    g.sport = "nba"
    assert P.spots(g, {"home": "lost", "away": "won"}) == [
        ("postseason", None), ("postseason, lost the last series game", "home"),
        ("postseason, won the last series game", "away")]
    reg = _g(1, "A", "B", 1, 0)
    reg.sport, reg.neutral = "ncaab", True
    assert P.spots(reg, {}) == [("neutral site, regular season", None)]
    reg.sport = "nba"
    assert P.spots(reg, {}) == []  # NBA neutral games are a handful of international ones: not a spot
    assert P.side_of("lock-price fav ML", g) == "home" and P.side_of("dog ML", g) == "away"


def test_series_spot_only_counts_bets_on_that_side():
    games = [_post(0, "A", "B", 1, 0), _post(1, "A", "B", 1, 0)]
    for g in games:
        g.sport = "nba"
    recs, control = {}, {}
    P.study(games, "nba", recs, control, n_random=2)
    # Game 2: home A won game 1, away B lost it. The away dog (+130) is the "lost last game" side.
    lost = recs[("nba", "dog ML: postseason, lost the last series game")]
    assert lost["in_sample"].bets + lost["blind"].bets == 1
    assert ("nba", "lock-price fav ML: postseason, lost the last series game") not in recs
    assert ("nba", "over: postseason, lost the last series game") not in recs


def test_nfl_loader_marks_playoffs(tmp_path):
    head = ("game_id,season,game_type,week,gameday,weekday,away_team,away_score,home_team,home_score,location,"
            "away_rest,home_rest,away_moneyline,home_moneyline,spread_line,away_spread_odds,home_spread_odds,"
            "total_line,under_odds,over_odds,div_game,roof,temp,wind,away_qb_id,home_qb_id,espn\n")
    rows = ("2015_01_PIT_NE,2015,REG,1,2015-09-10,Thursday,PIT,21,NE,28,Home,7,7,305,-350,7.5,-111,101,51,-107,-103,0,outdoors,65,7,a,b,1\n"
            "2015_19_KC_HOU,2015,WC,19,2016-01-09,Saturday,KC,30,HOU,0,Home,7,7,-160,140,-3,-110,-110,40,-110,-110,0,closed,,,a,b,2\n")
    (tmp_path / "2015.csv").write_text(head + rows)
    games = P.load_nfl(tmp_path)
    assert [g.stype for g in games] == ["2", "3"]
    assert games[0].spread == -7.5 and games[0].ml == {"home": -350, "away": 305}  # nflverse: + = home favored


def test_run_with_no_data_is_empty(tmp_path):
    out = P.run(tmp_path, tmp_path)
    assert out["rows"] == [] and out["control"]["spots"] == 0


def test_espn_marks_postseason_games():
    from sportsedge.data.espn import _parse_event
    from tests.test_parsers import espn_event

    ev = espn_event("9", "Boston Celtics", "Miami Heat", 100, 90)
    assert _parse_event(ev).postseason is False
    ev["season"] = {"year": 2026, "type": 3, "slug": "post-season"}
    assert _parse_event(ev).postseason is True


def test_the_lock_never_takes_a_postseason_game():
    from sportsedge.data.sample import SampleProvider
    from sportsedge.edge import Settings
    from sportsedge.engine import Engine
    from sportsedge.sports import resolve
    from sportsedge.tiers import build_cards

    provider = SampleProvider(seed=5)
    engine = Engine(provider, Settings(), now=provider.now)
    scans = [engine.scan(c) for c in resolve("nba,nhl,mlb")]
    s = Settings(proven=set())
    (lock,) = [c for c in build_cards(scans, s, provider.now) if c.tier == "lock"]
    # That game turns out to be a playoff game: a different game becomes the Lock.
    for scan in scans:
        if lock.pick.event_id in scan.contexts:
            scan.contexts[lock.pick.event_id].postseason = True
    (again,) = [c for c in build_cards(scans, s, provider.now) if c.tier == "lock"]
    assert again.pick.event_id != lock.pick.event_id
    # Every game is a playoff game: still a Lock every day, but never with money on it.
    for scan in scans:
        for ctx in scan.contexts.values():
            ctx.postseason = True
    for settings in (s, replace(s, lock_units=0.5), Settings()):
        for c in build_cards(scans, settings, provider.now):
            if c.tier == "lock":
                assert c.units == 0 and c.reasons[0].startswith("Playoff game, so no money")


def test_d5_lock_skips_postseason_games():
    from tests.test_d5 import NOW, side
    from sportsedge.d5.board import choose

    lk = side(-140, 0.70, 0.70, p_market=0.58, sport="nba", eid="n1")
    lk.w_p = lk.p
    assert [t for t, *_ in choose([lk], NOW) if t == "lock"] == ["lock"]
    lk.postseason = True
    assert not [t for t, *_ in choose([lk], NOW) if t == "lock"]
