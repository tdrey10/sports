"""The blueprint's money rules: each one gets a test so it never quietly comes back."""

from dataclasses import replace

from sportsedge.data.espn import parse_injuries
from sportsedge.data.sample import SampleProvider
from sportsedge.edge import Settings, qualifying
from sportsedge.engine import Engine, injury_factor
from sportsedge.models.game import predict_game
from sportsedge.sports import SPORTS, resolve
from sportsedge.tiers import build_cards

from tests.test_ledger import make_pick


def test_never_bet_worse_than_minus_150():
    s = Settings(min_ev=0.0)
    ok = make_pick(american=-150, ev=0.03)
    too_short = make_pick(american=-151, ev=0.03, event_id="ev2")
    assert qualifying([ok, too_short], s) == [ok]


def test_injury_report_lists_every_team_with_positions():
    report = parse_injuries({"injuries": [
        {"displayName": "Buffalo Bills", "injuries": [
            {"athlete": {"displayName": "Josh Allen", "position": {"abbreviation": "QB"}}, "status": "Out"}]},
        {"displayName": "Miami Dolphins", "injuries": []},
    ]})
    assert report["Buffalo Bills"] == [("Josh Allen", "Out", "QB")]
    assert report["Miami Dolphins"] == []  # listed with nobody out: known healthy, not unknown


def test_nfl_qb_out_costs_about_eight_points_of_win_chance():
    from sportsedge.models.ratings import TeamRatings

    cfg = SPORTS["nfl"]
    r = TeamRatings(link="identity", mu=22.0, hfa=0.0, off={"A": 0.0, "B": 0.0}, deff={"A": 0.0, "B": 0.0})
    base = predict_game(cfg, r, "A", "B", neutral=True).p_home
    eh, _ = r.expected_scores("A", "B", True)
    hurt = predict_game(cfg, r, "A", "B", neutral=True, home_factor=injury_factor(cfg, 0.08, eh)).p_home
    assert 0.06 < base - hurt < 0.10
    assert injury_factor(SPORTS["nhl"], 0.08, 3.0) == 1.0  # weights only where the blueprint sets them


def test_unknown_injuries_mean_no_bet():
    provider = SampleProvider(seed=5)
    engine = Engine(provider, Settings(), now=provider.now)
    scans = [engine.scan(c) for c in resolve("nba,epl")]
    epl = next(s for s in scans if s.sport.key == "epl")
    assert epl.contexts and all(ctx.injuries_unknown for ctx in epl.contexts.values())  # no soccer injury feed
    nba = next(s for s in scans if s.sport.key == "nba")
    assert nba.contexts and not any(ctx.injuries_unknown for ctx in nba.contexts.values())
    cards = build_cards(scans, engine.settings, provider.now)
    soccer = [c for c in cards if c.sport == "epl"]
    assert all(c.tier == "lean" and c.units == 0 for c in soccer)
    assert all(c.reasons[0].startswith("No bet: no injury report") for c in soccer)
    assert any(c.units > 0 for c in cards if c.sport == "nba")


def test_failed_injury_feed_is_unknown_not_healthy():
    provider = SampleProvider(seed=5)

    def broken(cfg):
        raise RuntimeError("feed down")

    provider.injuries = broken
    engine = Engine(provider, Settings(), now=provider.now)
    scan = engine.scan(SPORTS["nba"])
    assert scan.contexts and all(ctx.injuries_unknown for ctx in scan.contexts.values())
    assert not [c for c in build_cards([scan], engine.settings, provider.now) if c.units > 0]


def test_unproven_bet_types_are_sized_at_half_a_unit():
    from sportsedge.tiers import units_for

    big = make_pick(kelly=0.03, sport="nfl", market="spreads")
    assert units_for(big) == 3.0  # no studies loaded: normal quarter-Kelly sizing
    assert units_for(big, Settings(proven=set())) == 0.5
    assert units_for(big, Settings(proven={"nfl:spreads"})) == 3.0
    assert units_for(big, Settings(proven={"nfl:over/under", "nba:spreads"})) == 0.5


def test_a_lock_and_a_dog_every_morning():
    from sportsedge.ledger import Ledger
    from sportsedge.tiers import DOG_MAX_ODDS, DOG_MIN_ODDS

    provider = SampleProvider(seed=5)
    strict = Settings(min_ev=0.5, fill=True)  # nothing can clear this bar; no-unit fill switched on
    engine = Engine(provider, strict, now=provider.now)
    scans = [engine.scan(c) for c in resolve("nba,nhl,mlb")]
    cards = build_cards(scans, strict, provider.now)
    heads = {c.tier: c for c in cards if c.tier in ("lock", "dog")}
    assert set(heads) == {"lock", "dog"}  # a Lock and a Dog every day
    assert all(c.units == 0 for c in heads.values())  # no units without a real edge
    assert heads["lock"].reasons[0].startswith("Today's most likely winner")
    assert heads["dog"].reasons[0].startswith("Lean ")
    assert DOG_MIN_ODDS <= heads["dog"].pick.american <= DOG_MAX_ODDS
    assert heads["lock"].pick.american >= -150

    # A no-unit pick this morning doesn't block a real Lock or Dog later in the day.
    ledger = Ledger()
    ledger.publish(cards, provider.now)
    assert ledger.taken_today(provider.now) >= {"lock_lean", "dog_lean"}
    assert not {"lock", "dog"} & ledger.taken_today(provider.now)
    again = build_cards(scans, strict, provider.now, taken_today=ledger.taken_today(provider.now))
    assert not [c for c in again if c.tier in ("lock", "dog")]  # no second lean the same day


def test_lock_and_dog_even_when_no_side_shows_an_edge():
    provider = SampleProvider(seed=5)
    strict = Settings(min_ev=0.5, fill=True)
    engine = Engine(provider, strict, now=provider.now)
    scans = [engine.scan(c) for c in resolve("nba,nhl,mlb")]
    for scan in scans:
        for p in scan.picks:
            p.ev = -0.02  # the market is right about everything today
    heads = {c.tier: c for c in build_cards(scans, strict, provider.now) if c.tier in ("lock", "dog")}
    assert set(heads) == {"lock", "dog"} and all(c.units == 0 for c in heads.values())
    # By default nothing unbacked is posted: no maybes on the board.
    plain = Settings(min_ev=0.5)
    no_units = [c for c in build_cards(scans, plain, provider.now) if c.units == 0]
    assert [c.tier for c in no_units] == ["lock"]  # except the daily Lock, which is always posted
    assert not [c for c in build_cards(scans, Settings(min_ev=0.5, daily_lock=False), provider.now) if c.units == 0]


def test_only_bets_backed_by_a_blind_test_are_posted():
    provider = SampleProvider(seed=5)
    engine = Engine(provider, Settings(), now=provider.now)
    scans = [engine.scan(c) for c in resolve("nba,nhl,mlb")]
    assert build_cards(scans, Settings(), provider.now)  # no studies loaded: open
    nothing = build_cards(scans, Settings(proven=set()), provider.now)  # studies loaded, nothing proven
    assert [(c.tier, c.units) for c in nothing] == [("lock", 0.0)]  # only the daily Lock, no units
    only = [c for c in build_cards(scans, Settings(proven={"nba:spreads"}), provider.now) if c.units > 0]
    assert only and all(c.pick.sport == "nba" and c.pick.market == "spreads" for c in only)


def test_a_lock_must_pass_every_strict_check():
    from sportsedge.tiers import LOCK_MIN_CASH, LOCK_MIN_MARKET, LOCK_MIN_PROB

    provider = SampleProvider(seed=5)
    engine = Engine(provider, Settings(), now=provider.now)
    scans = [engine.scan(c) for c in resolve("nba,nhl,mlb,nfl")]
    for c in build_cards(scans, engine.settings, provider.now):
        if c.tier == "lock" and c.units > 0:
            p = c.pick
            assert p.model_prob >= LOCK_MIN_PROB and p.market_prob >= LOCK_MIN_MARKET
            assert p.win_prob >= LOCK_MIN_CASH - 0.05 and p.american >= -150 and not p.warnings


def test_bets_the_blind_study_says_lose_are_flagged():
    from sportsedge.tiers import losing_kind, track_line

    pick = make_pick(sport="nba", market="spreads", american=-110)
    losing = {"bets": 120, "won": 50, "lost": 70, "roi": -0.12, "se": 0.05}
    luck = {"bets": 120, "won": 58, "lost": 62, "roi": -0.03, "se": 0.09}
    assert losing_kind(pick, Settings()) is None  # no studies: nothing to go on
    assert losing_kind(pick, Settings(track={"nba:spreads": luck})) is None  # within luck
    assert losing_kind(pick, Settings(track={"nba:spreads": losing})) == "spreads"
    assert losing_kind(pick, Settings(track={"nba:coin flips (-149 to +149)": losing})) == "coin flips"
    assert losing_kind(pick, Settings(track={"nfl:spreads": losing})) is None  # other sport
    small = dict(losing, bets=12)
    assert losing_kind(pick, Settings(track={"nba:spreads": small})) is None  # too few bets to judge
    assert "spreads 58-62 (-3.0%)" in track_line(pick, Settings(track={"nba:spreads": luck}))


def test_only_legitimate_edges_get_units():
    from sportsedge.tiers import not_legit

    ok = make_pick(sport="nfl", market="spreads", american=-110, win_prob=0.55, market_prob=0.52)
    assert not_legit(ok, Settings()) is None
    assert "market price" in not_legit(make_pick(market_prob=None), Settings())
    assert "only 22%" in not_legit(make_pick(american=400, win_prob=0.22, market_prob=0.18), Settings(), min_win=0.45)
    losing = {"bets": 23, "won": 6, "lost": 17, "roi": -0.5, "se": 0.2}
    assert "lost in the blind study" in not_legit(ok, Settings(track={"nfl:all": losing}))


def test_open_picks_that_fail_the_bar_are_withdrawn():
    from datetime import timedelta

    from sportsedge.ledger import Ledger
    from tests.test_ledger import NOW, card

    led = Ledger()
    good = make_pick(event_id="1", win_prob=0.55, market_prob=0.52)
    longshot = make_pick(event_id="2", market="h2h", side="away", point=None, american=425, win_prob=0.2, market_prob=0.18)
    nomarket = make_pick(event_id="3", market_prob=None)
    led.publish([card(good, tier="early"), card(longshot, tier="early"), card(nomarket, tier="value")], NOW)
    dup = make_pick(event_id="abcdef0123456789abcdef0123456789", win_prob=0.55, market_prob=0.52)  # same game, other source
    led.publish([card(dup, tier="early")], NOW)
    pulled = led.withdraw(Settings(), NOW - timedelta(hours=1))
    assert {e.event_id for e in pulled} == {"2", "3", "abcdef0123456789abcdef0123456789"}
    assert all(e.status == "void" and e.reasons[0].startswith("Withdrawn") for e in pulled)
    assert next(e for e in led.entries.values() if e.event_id == "1").status == "pending"


def test_a_withdrawn_pick_can_come_back_as_the_daily_lock():
    from datetime import timedelta

    from sportsedge.ledger import Ledger
    from tests.test_ledger import NOW, card

    led = Ledger()
    p = make_pick(event_id="9", market_prob=None)
    led.publish([card(p, tier="value")], NOW)
    led.withdraw(Settings(), NOW - timedelta(hours=1))
    e = next(iter(led.entries.values()))
    assert e.status == "void" and "lock_lean" not in led.taken_today(NOW)
    led.publish([card(p, tier="lock", units=0.0)], NOW)
    assert e.status == "pending" and e.tier == "lock" and e.units == 0.0
    assert "lock_lean" in led.taken_today(NOW)


def test_clv_is_the_reward_that_adapts_trust():
    from datetime import timedelta

    from sportsedge.ledger import Ledger
    from sportsedge.tiers import backed, units_for
    from tests.test_ledger import NOW, card

    def ledger_with(clv_fair, n):
        led = Ledger()
        for i in range(n):
            led.publish([card(make_pick(event_id=f"e{i}", sport="nba", american=-110))], NOW)
        for e in led.entries.values():
            e.close_fair = clv_fair  # -110 pays 1.909: fair 0.50 -> CLV -4.5%, 0.55 -> +5%
        return led

    later = NOW + timedelta(days=1)
    bad = ledger_with(0.50, 60).clv_trust(later)["nba:spreads"]
    good = ledger_with(0.55, 60).clv_trust(later)["nba:spreads"]
    few = ledger_with(0.50, 5).clv_trust(later)["nba:spreads"]
    assert bad["factor"] == 0.0 and good["factor"] == 1.25 and few["factor"] == 1.0  # few picks: no change
    pick = make_pick(sport="nba", market="spreads", kelly=0.02)
    assert not backed(pick, Settings(clv_trust={"nba:spreads": 0.0}))
    assert units_for(pick, Settings(clv_trust={"nba:spreads": 0.5})) == 1.0
    assert units_for(pick) == 2.0
