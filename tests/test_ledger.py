import json
from datetime import datetime, timedelta, timezone

import pytest

from sportsedge.cli import main
from sportsedge.data.sample import SampleProvider
from sportsedge.edge import Pick, Settings
from sportsedge.engine import Engine
from sportsedge.ledger import Ledger, grade
from sportsedge.models.props import PlayerGame
from sportsedge.models.ratings import GameResult, fit_ratings
from sportsedge.runner import run_cycle, simulate_history
from sportsedge.site import build_data, render_html
from sportsedge.sports import SPORTS, resolve
from sportsedge.tiers import Card, build_cards, units_for

NOW = datetime(2026, 10, 8, 18, tzinfo=timezone.utc)
START = NOW + timedelta(hours=2)


def make_pick(market="spreads", side="home", point=-3.5, american=-110, player=None, ev=0.05, kelly=0.02,
              event_id="ev1", sport="nfl", win_prob=0.55, market_prob=0.52):
    return Pick(
        sport=sport, event_id=event_id, commence_time=START, matchup="Away @ Home", market=market,
        market_label=market, selection="sel", side=side, point=point, player=player, book="fanduel",
        american=american, model_prob=0.6, market_prob=market_prob, win_prob=win_prob, push_prob=0.0,
        fair_american=-120, ev=ev, kelly=kelly, stake=None, projection="", books=4,
    )


def card(pick, tier="value", units=1.0):
    return Card(tier=tier, units=units, headline="h", reasons=["r"], sport=pick.sport,
                sport_name="NFL", pick=pick, home="Home", away="Away")


def final(hs, as_):
    return GameResult("espn1", START, "Home", "Away", hs, as_, state="post")


@pytest.mark.parametrize("market,side,point,hs,as_,status", [
    ("spreads", "home", -3.5, 24, 20, "won"),
    ("spreads", "home", -3.0, 23, 20, "push"),
    ("spreads", "away", 3.5, 22, 20, "won"),
    ("spreads", "away", 3.5, 24, 20, "lost"),
    ("spreads", "home", -0.75, 21, 20, "half_won"),
    ("spreads", "home", -0.25, 20, 20, "half_lost"),
    ("totals", "over", 44.5, 24, 20, "lost"),
    ("totals", "under", 44.5, 24, 20, "won"),
    ("h2h", "away", None, 24, 20, "lost"),
    ("h2h", "draw", None, 1, 1, "won"),
])
def test_grading_game_lines(market, side, point, hs, as_, status):
    L = Ledger()
    (e,) = L.publish([card(make_pick(market, side, point, american=100))], NOW)
    grade(e, final(hs, as_), None, NOW + timedelta(hours=5))
    assert e.status == status
    expected = {"won": 1.0, "lost": -1.0, "push": 0.0, "half_won": 0.5, "half_lost": -0.5}[status]
    assert e.profit == pytest.approx(expected)


def test_grading_props_and_void():
    L = Ledger()
    p = make_pick("player_rush_yds", "over", 60.5, player="Ty Runner", american=-110)
    (e,) = L.publish([card(p)], NOW)
    box = [PlayerGame("espn1", START, "Ty Runner", "Home", {"rushing.YDS": 75.0})]
    grade(e, final(20, 10), box, NOW)
    assert e.status == "won" and e.result_value == 75
    e.status = "pending"
    grade(e, final(20, 10), [], NOW)
    assert e.status == "void" and e.profit == 0


def test_publish_keeps_first_price_and_one_play_per_game():
    L = Ledger()
    L.publish([card(make_pick(american=-110))], NOW)
    assert L.publish([card(make_pick(american=+105))], NOW) == []
    # A different market on the same game is not a second unit play...
    assert L.publish([card(make_pick("totals", "over", 44.5))], NOW) == []
    # ...but a prop on that game is fine.
    assert len(L.publish([card(make_pick("player_receptions", "over", 4.5, player="X"))], NOW)) == 1
    assert next(iter(L.entries.values())).odds == -110


def test_line_tracking_and_clv():
    L = Ledger()
    (e,) = L.publish([card(make_pick(american=+120, market_prob=0.45))], NOW)

    class Scan:
        picks = [make_pick(american=-105, market_prob=0.52)]
    L.update_prices([Scan()], NOW + timedelta(hours=1))
    assert e.current_odds == -105 and e.close_fair == 0.52
    assert e.clv == pytest.approx(0.52 * 2.2 - 1)
    assert len(e.history) == 2


def test_refresh_grades_and_tracks_live():
    L = Ledger()
    (e,) = L.publish([card(make_pick())], NOW)
    live = GameResult("espn1", START, "Home", "Away", None, None, state="in", detail="Q3", live_home=14, live_away=10)
    L.refresh(lambda s, a, b: [live], lambda s, g: [], START + timedelta(hours=1))
    assert e.status == "live" and e.live["detail"] == "Q3"
    L.refresh(lambda s, a, b: [final(27, 20)], lambda s, g: [], START + timedelta(hours=4))
    assert e.status == "won" and e.final == "Away 20 @ Home 27"


def test_save_load_roundtrip(tmp_path):
    L = Ledger()
    L.publish([card(make_pick())], NOW)
    L.save(tmp_path / "l.json")
    L2 = Ledger.load(tmp_path / "l.json")
    assert list(L2.entries) == list(L.entries)


def test_learn_weights_prefers_the_better_source():
    L = Ledger()
    import random
    rng = random.Random(0)
    for i in range(200):
        truth = rng.uniform(0.3, 0.7)
        model = min(max(truth + rng.gauss(0, 0.2), 0.05), 0.95)  # noisy model, sharp market
        (e,) = L.publish([card(make_pick(event_id=f"e{i}", market_prob=truth), units=0.0)], NOW)
        e.model_prob = model
        e.status = "won" if rng.random() < truth else "lost"
    w = L.learn_weights()
    assert w["nfl:game"] >= 0.7


def test_units_from_kelly():
    assert units_for(make_pick(kelly=0.012)) == 1.0
    assert units_for(make_pick(kelly=0.001)) == 0.0  # owner: not worth the money, no units
    assert units_for(make_pick(kelly=0.2)) == 3.0


def test_build_cards_tiers():
    provider = SampleProvider(seed=5)
    engine = Engine(provider, Settings(), now=provider.now)
    scans = [engine.scan(c, props=True) for c in resolve("nba,nhl,mlb")]
    cards = build_cards(scans, engine.settings, provider.now)
    tiers = [c.tier for c in cards]
    assert tiers.count("lock") <= 1 and tiers.count("dog") <= 1 and tiers.count("prop") <= 1
    lean_heads = [c for c in cards if c.reasons and c.reasons[0].startswith(("Lean ", "Today's most likely winner"))]
    assert all(c.units > 0 for c in cards if c.tier != "lean" and c not in lean_heads)
    assert all(c.units == 0 and c.tier in ("lock", "dog") and not c.best_rank for c in lean_heads)
    assert all(c.units == 0 for c in cards if c.tier == "lean")
    assert all(c.headline and c.reasons for c in cards)
    again = build_cards(scans, engine.settings, provider.now, taken_today={"lock", "dog", "prop"})
    assert not {"lock", "dog", "prop"} & {c.tier for c in again}


def test_rest_effects_are_learned():
    import random
    rng = random.Random(2)
    teams = [f"T{i}" for i in range(12)]
    games, t = [], NOW - timedelta(days=150)
    for k in range(1500):
        h, a = rng.sample(teams, 2)
        t += timedelta(hours=2.4)
        games.append((k, t, h, a))
    from sportsedge.models.ratings import rest_days, is_b2b
    raw = [GameResult(f"g{k}", t, h, a, 0, 0) for k, t, h, a in games]
    rest = rest_days(raw)
    results = []
    for g in raw:
        hb, ab = is_b2b(rest[(g.event_id, g.home)]), is_b2b(rest[(g.event_id, g.away)])
        hs = 110 + 2 - 4 * hb + rng.gauss(0, 8)
        as_ = 110 - 4 * ab + rng.gauss(0, 8)
        results.append(GameResult(g.event_id, g.date, g.home, g.away, hs, as_))
    r = fit_ratings(results, NOW, 1e6, 1.0, rest_effects=True)
    assert r.b2b_off == pytest.approx(-4, abs=1.5)


def test_simulated_history_and_dashboard(tmp_path):
    provider = SampleProvider(seed=11)
    L = Ledger()
    sports = resolve("nba,nhl")
    brains = simulate_history(provider, sports, L, Settings(), days=4, props=False)
    res = run_cycle(provider, sports, L, Settings(), provider.now)
    assert any(e.settled for e in L.entries.values())
    stats = L.stats(provider.now)
    assert stats["overall"]["won"] + stats["overall"]["lost"] > 0
    data = build_data(L, {**brains, **res.brains}, res.weights, sports, provider.now, demo=True)
    html = render_html(data)
    assert html.startswith("<!doctype html>") and "<title>Follow Me, Instead Of Your Dreams</title>" in html.split("<body>")[0]
    assert "__DATA__" not in html
    json.dumps(data)  # serialisable


def test_cli_run_demo(tmp_path, capsys):
    args = ["run", "nba", "--demo", "--history-days", "2",
            "--data-dir", str(tmp_path / "data"), "--site", str(tmp_path / "site")]
    assert main(args) == 0
    assert (tmp_path / "site" / "index.html").exists()
    assert (tmp_path / "data" / "demo-ledger.json").exists()
    assert main(args + ["--scores-only"]) == 0
    assert "dashboard:" in capsys.readouterr().out


def test_best_bets_ranked_by_edge_score():
    from sportsedge.tiers import BEST_BETS, BEST_MIN_SCORE
    from sportsedge.edge import growth
    safe = make_pick(american=-110, win_prob=0.56, ev=0.07)
    longshot = make_pick(american=450, win_prob=0.20, ev=0.10)
    assert growth(safe) > growth(longshot)
    provider = SampleProvider(seed=5)
    engine = Engine(provider, Settings(), now=provider.now)
    scans = [engine.scan(c, props=True) for c in resolve("nba,nhl")]
    cards = build_cards(scans, engine.settings, provider.now)
    ranks = sorted(c.best_rank for c in cards if c.best_rank)
    assert ranks == list(range(1, len(ranks) + 1)) and len(ranks) <= BEST_BETS
    best = sorted((c for c in cards if c.best_rank), key=lambda c: c.best_rank)
    assert [c.score for c in best] == sorted((c.score for c in best), reverse=True)
    assert all(c.score >= BEST_MIN_SCORE and not c.pick.warnings for c in best)
    assert len([c for c in build_cards(scans, engine.settings, provider.now, best_taken=BEST_BETS) if c.best_rank]) == 0


def test_key_numbers_learned_by_brain():
    import random
    from dataclasses import replace
    from sportsedge.brain import with_shapes
    rng = random.Random(3)
    def pts():
        return sum(rng.choice([0, 0, 3, 7]) for _ in range(4))
    games = [GameResult(f"g{i}", NOW - timedelta(days=200 - i / 5), "A", "B", pts(), pts()) for i in range(800)]
    cfg = with_shapes(SPORTS["nfl"], games, NOW)
    assert cfg.margin_shape[3] > 1.3 and cfg.margin_shape[7] > 1.3
    assert SPORTS["nfl"].margin_shape == {}  # the global config is untouched


def test_pitcher_factor_and_parse():
    from sportsedge.data.espn import _parse_event
    from sportsedge.models.game import pitcher_factor
    assert pitcher_factor(2.5) < 1 < pitcher_factor(6.0)
    ev = {"id": "1", "date": "2026-10-08T23:05Z", "status": {"type": {"completed": False, "state": "pre"}},
          "competitions": [{"competitors": [
              {"homeAway": "home", "team": {"displayName": "Yankees"},
               "probables": [{"athlete": {"displayName": "Gerrit Cole"}, "statistics": [
                   {"abbreviation": "W", "displayValue": "9"}, {"abbreviation": "L", "displayValue": "4"},
                   {"abbreviation": "ERA", "displayValue": "2.95"}]}]},
              {"homeAway": "away", "team": {"displayName": "Red Sox"}}]}]}
    g = _parse_event(ev)
    assert g.home_pitcher == "Gerrit Cole" and g.home_pitcher_era == 2.95 and g.away_pitcher == ""
    assert g.home_pitcher_decisions == 13
    assert pitcher_factor(6.0, 0) == 1.0  # one bad start says nothing yet


def test_prop_matchup_and_usage_factors():
    from sportsedge.models.props import matchup_factor, usage_factor
    spec = SPORTS["nba"].props["player_rebounds"]
    lines = []
    for i in range(10):
        for team, opp in (("Soft", f"X{i}"), ("Hard", f"Y{i}")):
            ev = f"{team}{i}"
            lines.append(PlayerGame(ev, NOW, f"{team} big", team, {"REB": 10.0}))
            lines.append(PlayerGame(ev, NOW, f"{opp} big", opp, {"REB": 14.0 if team == "Soft" else 8.0}))
    assert matchup_factor(spec, lines, "Soft") > 1.02
    assert matchup_factor(spec, lines, "Hard") < 0.98
    team_lines = [PlayerGame(f"t{i}", NOW + timedelta(days=i), p, "T", {"REB": v})
                  for i in range(5) for p, v in (("Star", 12.0), ("Role", 6.0))]
    f, gone = usage_factor(spec, team_lines, "T", {"Star"})
    assert gone == ["Star"] and f == pytest.approx(1.25)
    assert usage_factor(spec, team_lines, "T", set()) == (1.0, [])


def test_best_rank_promotes_existing_and_numbers_without_gaps():
    L = Ledger()
    early = make_pick(event_id="a")
    L.publish([card(early, tier="early")], NOW)
    c1 = card(early, tier="value"); c1.best_rank = 1
    c2 = card(make_pick(event_id="b")); c2.best_rank = 2
    L.publish([c2, c1], NOW)
    ranks = sorted((e.event_id, e.best_rank) for e in L.entries.values())
    assert ranks == [("a", 1), ("b", 2)]


def test_spread_headline_wording():
    provider = SampleProvider(seed=5)
    engine = Engine(provider, Settings(), now=provider.now)
    scans = [engine.scan(SPORTS["nfl"])]
    from sportsedge.insights import reasons
    spreads = [p for p in scans[0].picks if p.market == "spreads"]
    for p in spreads[:6]:
        headline, _ = reasons(p, scans[0])
        assert ("laying" in headline) == (p.point < 0)
        assert "to win by" in headline or "to lose by" in headline


def test_props_limited_by_sport_and_window():
    provider = SampleProvider(seed=4)
    L = Ledger()
    res = run_cycle(provider, resolve("nba,nhl"), L, Settings(), provider.now,
                    props=True, props_sports={"nba"}, props_hours=6)
    by_sport = {s.sport.key: s for s in res.scans}
    assert not any(p.is_prop for p in by_sport["nhl"].picks)
    nba_props = [p for p in by_sport["nba"].picks if p.is_prop]
    assert nba_props and all(p.commence_time <= provider.now + timedelta(hours=6) for p in nba_props)


def test_cli_page_from_saved_ledger(tmp_path):
    L = Ledger()
    L.publish([card(make_pick())], NOW)
    (tmp_path / "data").mkdir()
    L.save(tmp_path / "data" / "ledger.json")
    out = tmp_path / "page.html"
    assert main(["page", "--data-dir", str(tmp_path / "data"), "--out", str(out)]) == 0
    html = out.read_text()
    assert html.startswith("<title>Follow Me, Instead Of Your Dreams</title>") and "__DATA__" not in html


def test_longshots_capped_at_half_unit():
    assert units_for(make_pick(american=600, kelly=0.03)) == 0.5
    assert units_for(make_pick(american=250, kelly=0.03)) == 3.0


def test_cards_show_the_numbers_and_locks_pass_every_check():
    from sportsedge.tiers import LOCK_MAX_GAP, LOCK_MIN_PROB

    provider = SampleProvider(seed=5)
    engine = Engine(provider, Settings(), now=provider.now)
    scans = [engine.scan(c, props=True) for c in resolve("nba,nhl,mlb")]
    cards = build_cards(scans, engine.settings, provider.now)
    games = [c for c in cards if not c.pick.is_prop]
    assert games and all(any(r.startswith("Season ") for r in c.reasons) for c in games)
    assert all(any(r.startswith("Last 5") for r in c.reasons) for c in games)
    team_picks = [c for c in games if c.pick.market in ("h2h", "spreads") and c.pick.side in ("home", "away")]
    assert team_picks and all(any(r.startswith("Upset") for r in c.reasons) for c in team_picks)
    for c in cards:
        if c.tier == "lock" and c.units > 0:  # a real (units) Lock; the daily no-unit Lock is checked elsewhere
            p = c.pick
            assert p.model_prob >= LOCK_MIN_PROB and not p.warnings
            assert abs(p.model_prob - p.market_prob) <= LOCK_MAX_GAP
    props = [c for c in cards if c.pick.is_prop]
    assert props and all("of the last" in c.pick.projection for c in props)


def test_every_daily_lock_has_its_own_record():
    from sportsedge.ledger import Ledger

    ledger = Ledger()
    ledger.publish([card(make_pick(), tier="lock", units=0.0)], START)
    for e in ledger.entries.values():
        e.status, e.profit = "won", 0.91
    stats = ledger.stats(START)
    assert stats["lock_all"]["won"] == 1  # a no-unit daily Lock still counts in the Lock's own record
    assert stats["by_tier"].get("lock", {"won": 0})["won"] == 0  # but not in the money record


def test_home_screen_site_has_its_icon_and_name(tmp_path):
    import json as _json

    from sportsedge.site import write_app

    index = write_app(tmp_path, {"entries": [], "stats": {}})
    html = index.read_text()
    assert '<link rel="apple-touch-icon" href="apple-touch-icon.png">' in html
    assert 'apple-mobile-web-app-title" content="Follow Me"' in html
    manifest = _json.loads((tmp_path / "manifest.webmanifest").read_text())
    assert manifest["short_name"] == "Follow Me" and manifest["display"] == "standalone"
    for icon in ["apple-touch-icon.png"] + [i["src"] for i in manifest["icons"]]:
        assert (tmp_path / icon).read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_board_drops_at_8am_local():
    from dataclasses import replace

    from sportsedge.data.sample import SampleProvider
    from sportsedge.ledger import Ledger
    from sportsedge.runner import run_cycle
    from sportsedge.sports import resolve

    provider = SampleProvider(seed=5)
    hour = provider.now.astimezone(Ledger().tz).hour
    held = run_cycle(provider, resolve("nba,nhl"), Ledger(), Settings(board_hour=hour + 1), provider.now)
    assert held.published == 0  # before the board drops: lines watched, nothing posted
    open_ = run_cycle(provider, resolve("nba,nhl"), Ledger(), Settings(board_hour=hour), provider.now)
    assert open_.published > 0


def test_withdraw_uses_the_settings_favorite_limit():
    # The limit lives in one place (Settings.max_favorite); the ledger must not hard-code its own.
    import inspect

    from sportsedge.ledger import Ledger
    src = inspect.getsource(Ledger.withdraw)
    assert "settings.max_favorite" in src and "-150" not in src


def test_page_data_carries_espn_scoreboards_for_live_scores():
    # The page pulls live scores from ESPN itself between engine runs; it needs each sport's path.
    from datetime import datetime, timezone

    data = build_data(Ledger(), {}, {}, resolve("nhl,ncaaf"), datetime(2026, 10, 10, tzinfo=timezone.utc))
    assert data["espn"]["nhl"] == {"path": "hockey/nhl", "groups": None}
    assert data["espn"]["ncaaf"]["groups"] == "80"  # college boards need the division group
    html = render_html(data)
    assert "site.api.espn.com/apis/site/v2/sports/" in html and "pollScores" in html


def test_daily_lock_promotes_a_side_already_posted_as_a_lean():
    from datetime import timedelta

    now = START - timedelta(hours=5)
    led = Ledger()
    p = make_pick(event_id="a", american=-120, win_prob=0.58)
    led.publish([card(p, tier="lean", units=0.0)], now)
    added = led.publish([card(p, tier="lock", units=0.0)], now + timedelta(minutes=30))
    (e,) = led.entries.values()
    assert e.tier == "lock" and added == [e]  # the lean became today's Lock, not a skipped duplicate
    assert "lock_lean" in led.taken_today(now)


def test_a_small_stake_lock_stays_while_it_beats_break_even():
    s = Settings(proven=set())
    L = Ledger()
    p = make_pick(market="h2h", side="home", point=None, american=-130, win_prob=0.60, ev=0.04)
    (e,) = L.publish([card(p, tier="lock", units=0.5)], NOW)
    assert not L.withdraw(s, NOW)  # unproven, but the Lock's small stake is the owner's rule
    e.win_prob = 0.55  # -130 needs 56.5%
    assert L.withdraw(s, NOW) == [e] and e.status == "void"


def test_record_by_sport_counts_every_graded_pick_and_the_unit_plays():
    L = Ledger()
    picks = [("nfl", 1.0, "won", 0.91), ("nfl", 0.0, "lost", -1.0), ("nhl", 0.0, "won", 1.2), ("nhl", 0.0, "void", 0.0)]
    for i, (sport, units, status, profit) in enumerate(picks):
        p = make_pick(event_id=f"e{i}", sport=sport)
        (e,) = L.publish([card(p, tier="value" if units else "lean", units=units)], NOW)
        e.status, e.profit = status, profit
    by = L.stats(NOW)["by_sport"]
    assert (by["nfl"]["won"], by["nfl"]["lost"]) == (1, 0)  # unit plays
    assert (by["nfl"]["all"]["won"], by["nfl"]["all"]["lost"]) == (1, 1)  # every graded pick
    assert by["nhl"]["won"] + by["nhl"]["lost"] == 0 and by["nhl"]["all"]["won"] == 1  # void left out
    assert abs(by["nhl"]["all"]["roi"] - 1.2) < 1e-9  # a lean counts as 1 unit risked


def test_overall_record_counts_every_pick_put_out():
    L = Ledger()
    for i, (units, status, profit) in enumerate([(1.0, "won", 0.91), (0.0, "lost", -1.0), (0.0, "won", 1.5),
                                                  (0.0, "void", 0.0)]):
        (e,) = L.publish([card(make_pick(event_id=f"e{i}"), tier="value" if units else "lean", units=units)], NOW)
        e.status, e.profit = status, profit
    st = L.stats(NOW)
    assert (st["all_picks"]["won"], st["all_picks"]["lost"]) == (2, 1)  # void left out
    assert (st["overall"]["won"], st["overall"]["lost"]) == (1, 0)  # money plays only
    assert abs(st["all_picks"]["units"] - 1.41) < 1e-9
