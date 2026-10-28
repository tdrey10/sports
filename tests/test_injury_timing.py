"""Owner, Oct 28: when a player is ruled out, do the books move far enough, fast enough?"""

import csv
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

from sportsedge import injurytiming
from sportsedge.archive import SNAPSHOT_FIELDS

T0 = datetime(2026, 10, 28, 15, tzinfo=timezone.utc)
START = T0 + timedelta(hours=8)


def scan(out=(), questionable=(), unknown=()):
    ctx = NS(event=NS(commence_time=START), home="Home", away="Away", injuries_unknown=list(unknown),
             injured={"Home": [(p, "G") for p in out]}, questionable={"Home": [(p, "F") for p in questionable]})
    return NS(sport=NS(key="nba", has_injury_feed=True), contexts={"g1": ctx})


def rows(tmp_path):
    with (tmp_path / "injuries" / "nba" / "2026-10.csv").open() as f:
        return list(csv.DictReader(f))


def test_only_changes_are_logged_and_a_first_look_is_not_news(tmp_path):
    out = tmp_path / "injuries"
    assert injurytiming.log(out, [scan(out=["Long Out"])], T0, root=tmp_path) == 0  # first look: a baseline
    assert injurytiming.log(out, [scan(out=["Long Out"], questionable=["Star"])], T0 + timedelta(minutes=30), root=tmp_path) == 1
    assert injurytiming.log(out, [scan(out=["Long Out", "Star"])], T0 + timedelta(hours=1), root=tmp_path) == 1
    assert injurytiming.log(out, [scan(out=["Star"])], T0 + timedelta(hours=2), root=tmp_path) == 1  # Long Out returns
    got = [(r["player"], r["was"], r["now"]) for r in rows(tmp_path)]
    assert got == [("Star", "active", "questionable"), ("Star", "questionable", "out"), ("Long Out", "out", "active")]


def test_a_team_with_no_report_is_not_logged(tmp_path):
    out = tmp_path / "injuries"
    injurytiming.log(out, [scan()], T0, root=tmp_path)
    assert injurytiming.log(out, [scan(out=["Star"], unknown=["Home"])], T0 + timedelta(hours=1), root=tmp_path) == 0


def _snap(w, at, home, away):
    for side, price in (("home", home), ("away", away)):
        w.writerow({"seen_at": at.isoformat(), "event_id": "g1", "start": START.isoformat(), "home": "Home",
                    "away": "Away", "market": "h2h", "side": side, "point": "", "book": "draftkings", "price": price})


def test_the_study_measures_the_move_on_the_news_and_after_it(tmp_path):
    out = tmp_path / "injuries"
    injurytiming.log(out, [scan()], T0, root=tmp_path)
    news = T0 + timedelta(hours=2)
    injurytiming.log(out, [scan(out=["Star"])], news, root=tmp_path)
    (tmp_path / "odds" / "nba").mkdir(parents=True)
    with (tmp_path / "odds" / "nba" / "2026-10.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=SNAPSHOT_FIELDS)
        w.writeheader()
        _snap(w, T0, -150, 130)  # before the news
        _snap(w, news, -130, 110)  # the book moved some when we saw it
        _snap(w, START - timedelta(minutes=10), -110, -110)  # and kept moving to the close
    (tmp_path / "games" / "nba").mkdir(parents=True)
    with (tmp_path / "games" / "nba" / "2026-10.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["event_id", "home_score", "away_score"])
        w.writeheader()
        w.writerow({"event_id": "g1", "home_score": 100, "away_score": 104})
    [e] = injurytiming.events("nba", tmp_path)
    assert e["news_move"] < 0 and e["drift"] < 0  # the injured team's chance fell on the news, then fell more
    assert e["bet_price"] == 110 and e["clv"] > 0 and e["ret"] == 1.1  # the other team at +110 beat the close and won
    res = injurytiming.run("nba", tmp_path)
    assert res["all"]["drift_same_way"] == 1.0 and res["all"]["verdict"] == "collecting"
    assert "kept moving the same way" in injurytiming.report(res)


def test_once_it_passes_blind_fresh_news_posts_the_other_team_with_units(tmp_path):
    """Owner, Oct 28: 'I want the pick to have units if it is proven to have good value.'"""
    import dataclasses
    import json

    from sportsedge import proven
    from tests.test_ledger import make_pick

    out = tmp_path / "injuries"
    injurytiming.log(out, [scan()], T0, root=tmp_path)
    news = T0 + timedelta(hours=2)
    injurytiming.log(out, [scan(out=["Star"])], news, root=tmp_path)
    passed = {"verdict": "PROVEN", "blind": {"bets": 120, "flat_roi": 0.09, "se": 0.07},
              "in_sample": {"bets": 120, "flat_roi": 0.04, "se": 0.08}}
    (tmp_path / "injury_timing.json").write_text(json.dumps({"nba": {"all": passed}}))
    away = dataclasses.replace(make_pick(market="h2h", side="away", point=None, american=120, sport="nba", event_id="g1"),
                               commence_time=START)
    s = scan(out=["Star"])
    s.picks = [away]
    s.sport.name = "NBA"
    for ctx in s.contexts.values():
        ctx.neutral, ctx.wind_mph = False, None
    [c] = proven.find([s], news + timedelta(minutes=5), tmp_path)
    assert (c.pick.side, c.units, c.proven.split(":")[0]) == ("away", 0.5, "PROVEN")
    assert "Home just ruled out Star" in c.headline
    # Stale news (an hour old) doesn't post: the study bet at the price when the news broke.
    assert proven.find([s], news + timedelta(hours=1), tmp_path) == []
    # Still collecting: no units.
    (tmp_path / "injury_timing.json").write_text(json.dumps({"nba": {"all": {**passed, "verdict": "collecting"}}}))
    assert proven.find([s], news + timedelta(minutes=5), tmp_path) == []


def test_or_edge_beating_the_close_blind_earns_half_a_unit_before_the_record_is_long_enough(tmp_path, monkeypatch):
    """Owner, Oct 28: units if it's proven to have good value 'or edge'. Beating the closing line needs no
    final score, so it can prove itself first: 1/2u, grade B."""
    import dataclasses

    from sportsedge import proven, scorecard
    from tests.test_ledger import make_pick

    def ev(i, clv):
        return {"seen_at": f"2026-10-{1 + i // 10:02d}T{i % 10:02d}:00", "event_id": f"g{i}", "team": "T", "pos": "",
                "tier": "", "news_move": -0.03, "drift": -0.01, "clv": clv}
    rows = [ev(i, 0.02 if i % 4 else -0.01) for i in range(120)]  # beats the close 75%, no finals yet
    monkeypatch.setattr(injurytiming, "events", lambda sport, root="data": rows)
    res = injurytiming.run("nba")
    assert res["all"]["verdict"] == "CLV" and res["all"]["graded"] == 0
    hit, _ = injurytiming.spot(res, {"tier": ""})
    pick = dataclasses.replace(make_pick(market="h2h", side="away", point=None, american=120, sport="nba"))
    c = proven._card("NBA", NS(home="Home", away="Away"), pick, hit, "Away: Home just ruled out Star")
    assert c.units == 0.5 and c.proven.startswith("CLV") and c.pick.ev > 0
    assert scorecard.grade(c)[0] == "B"
    # Beating the close only half the time is no edge.
    monkeypatch.setattr(injurytiming, "events", lambda sport, root="data": [ev(i, 0.02 if i % 2 else -0.02) for i in range(120)])
    assert injurytiming.run("nba")["all"]["verdict"] == "collecting"


def test_a_key_questionable_player_holds_a_money_pick_once_the_study_says_hold(tmp_path, monkeypatch):
    """Owner, Oct 28: if betting into a star's unresolved questionable tag loses to the close, wait."""
    import json

    from sportsedge import runner
    from sportsedge.tiers import Card
    from tests.test_ledger import make_pick

    rows = [{"seen_at": f"2026-10-{1 + i // 10:02d}T{i % 10:02d}:00", "clv": -0.02 if i % 4 else 0.01} for i in range(120)]
    q = injurytiming._hold_verdict(rows)
    assert q["verdict"] == "HOLD"  # beat the close only 25% of the time
    assert injurytiming._hold_verdict(rows[:50])["verdict"] == "collecting"
    (tmp_path / "injury_timing.json").write_text(json.dumps({"nba": {"questionable": q}}))
    monkeypatch.setattr(injurytiming, "_tiers", lambda sport, root: {"Home": {"tiers": {"Star": "Key", "Bench": "Rotation"}}})
    pick = make_pick(market="h2h", side="home", point=None, american=-120, sport="nba", event_id="g1")
    card = Card(tier="lock", units=1.0, headline="h", reasons=["r"], sport="nba", sport_name="NBA", pick=pick,
                home="Home", away="Away", proven="STRONG: test")
    ctx = NS(home="Home", away="Away", questionable={"Home": [("Star", "G")]})
    scan_ = NS(sport=NS(key="nba"), contexts={"g1": ctx})
    assert runner._hold_questionable([card], [scan_], str(tmp_path)) == 1
    assert (card.units, card.tier) == (0.0, "lean") and "Star is questionable" in card.reasons[0]
    # A bench player's tag holds nothing.
    card2 = Card(tier="value", units=1.0, headline="h", reasons=["r"], sport="nba", sport_name="NBA", pick=pick,
                 home="Home", away="Away", proven="STRONG: test")
    ctx.questionable = {"Home": [("Bench", "F")]}
    assert runner._hold_questionable([card2], [scan_], str(tmp_path)) == 0 and card2.units == 1.0
