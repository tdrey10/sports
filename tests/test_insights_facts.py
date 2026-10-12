
from datetime import timedelta

from sportsedge.data.sample import SampleProvider
from sportsedge.edge import Settings
from sportsedge.engine import Engine
from sportsedge.insights import add_facts, matchup_facts, streak
from sportsedge.models.ratings import GameResult
from sportsedge.sports import resolve
from sportsedge.tiers import Card


def test_streak_and_matchup_facts():
    t0 = SampleProvider(seed=1).now
    games = [GameResult(str(i), t0 - timedelta(days=10 - i), "A", "B", 3 if i < 6 else 1, 2 if i < 6 else 4)
             for i in range(9)]
    assert streak(games, "A") == ("lost", 3) and streak(games, "B") == ("won", 3)
    facts = matchup_facts(games, "A", "B", t0, "nhl")
    text = " | ".join(facts)
    assert "B (3-6) @ A (6-3)" in text and "B has won 3 straight" in text and "Head to head" in text


def test_sharp_card_gets_game_facts_from_the_scan():
    provider = SampleProvider(seed=5)
    scan = Engine(provider, Settings(), now=provider.now).scan(resolve("nba")[0])
    eid, ctx = next(iter(scan.contexts.items()))
    pick = next(p for p in scan.picks if p.event_id == eid) if any(p.event_id == eid for p in scan.picks) else scan.picks[0]
    ctx = scan.contexts[pick.event_id]
    card = Card(tier="sharp", units=0.5, headline="h", reasons=["price 1", "price 2", "Bet it at this book."],
                sport="nba", sport_name="NBA", pick=pick, home=ctx.home, away=ctx.away)
    add_facts(card, [scan])
    assert 5 <= len(card.reasons) <= 10
    assert card.reasons[:2] == ["price 1", "price 2"] and card.reasons[-1] == "Bet it at this book."
    assert any(r.startswith("Our projected score") for r in card.reasons)
