"""Owner, Nov 5: say why that team should win, in stats (streaks, home record, rest, the other side's injuries)."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from sportsedge.insights import CASE_MARK, add_case, case_for
from sportsedge.models.ratings import GameResult

T0 = datetime(2026, 10, 20, 23, tzinfo=timezone.utc)


def _g(i, home, away, hs, as_):
    return GameResult(event_id=f"g{i}", date=T0 + timedelta(days=i), home=home, away=away, home_score=hs, away_score=as_)


def _season():
    games = []
    for i in range(5):  # Hawks win 5 straight at home, by 10
        games.append(_g(i, "Hawks", f"X{i}", 120, 110))
    for i in range(5):  # Wizards lose 4 on the road
        games.append(_g(10 + i, f"Y{i}", "Wizards", 115, 100 if i else 120))
    return games


def test_the_case_names_the_streak_the_home_record_and_the_other_sides_slump():
    when = T0 + timedelta(days=20)
    ctx = SimpleNamespace(home_b2b=False, away_b2b=True, home_rest=2.0, away_rest=1.0, injuries={"Wizards": ["Kyle Kuzma"]})
    lines = case_for(_season(), "Hawks", "Wizards", True, when, "nba", ctx)
    assert len(lines) == 4
    assert "Hawks have won 5 straight." in lines
    assert "Hawks are 5-0 at home this season." in lines
    assert "Wizards are just 1-4 on the road." in lines  # the 4 strongest; rest and injuries rank below these


def test_rest_and_the_other_sides_injuries_show_when_nothing_bigger_does():
    when = T0 + timedelta(days=20)
    ctx = SimpleNamespace(home_b2b=False, away_b2b=True, home_rest=2.0, away_rest=1.0, injuries={"Wizards": ["Kyle Kuzma"]})
    lines = case_for([], "Hawks", "Wizards", True, when, "nba", ctx)
    assert lines == ["Wizards played last night; Hawks come in rested.", "Wizards are missing Kyle Kuzma."]


def test_add_case_puts_the_lines_on_a_side_pick_once_and_skips_totals():
    when = T0 + timedelta(days=20)
    ctx = SimpleNamespace(home="Hawks", away="Wizards", event=SimpleNamespace(commence_time=when), neutral=False,
                          home_b2b=False, away_b2b=False, home_rest=None, away_rest=None, injuries={})
    scan = SimpleNamespace(sport=SimpleNamespace(key="nba"), results=_season(), contexts={"e1": ctx}, ratings=None)
    e = SimpleNamespace(sport="nba", market="h2h", side="home", home="Hawks", away="Wizards", start=when,
                        player=None, reasons=["Lean · no units", "We give them 57%."])
    assert add_case(e, [scan]) and e.reasons[2].startswith(CASE_MARK)
    n = len(e.reasons)
    assert not add_case(e, [scan]) and len(e.reasons) == n  # once
    over = SimpleNamespace(sport="nba", market="totals", side="over", home="Hawks", away="Wizards", start=when,
                           player=None, reasons=["x"])
    assert not add_case(over, [scan])
