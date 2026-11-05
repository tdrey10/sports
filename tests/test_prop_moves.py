"""Owner, Nov 5: where the money is going on props (biggest line moves today)."""
from datetime import datetime, timezone

from sportsedge import propmoves

NOW = datetime(2026, 11, 4, 20, tzinfo=timezone.utc)
HEAD = "seen_at,event_id,start,home,away,player,market,line,over_price,under_price,book\n"


def _row(seen, player, market, line, start="2026-11-05T00:00:00+00:00"):
    return f"{seen},e1,{start},Pistons,76ers,{player},{market},{line},,,draftkings_est\n"


def test_biggest_moves_first_and_the_side_the_money_is_on(tmp_path):
    d = tmp_path / "props" / "nba"
    d.mkdir(parents=True)
    (d / "2026-11.csv").write_text(HEAD
        + _row("2026-11-04T15:00:00+00:00", "Cade Cunningham", "player_assists", 8.5)
        + _row("2026-11-04T22:00:00+00:00", "Cade Cunningham", "player_assists", 9.5)
        + _row("2026-11-04T15:00:00+00:00", "Joel Embiid", "player_points", 30.5)
        + _row("2026-11-04T22:00:00+00:00", "Joel Embiid", "player_points", 28.5)
        + _row("2026-11-04T15:00:00+00:00", "Tyrese Maxey", "player_points", 25.5)
        + _row("2026-11-04T15:00:00+00:00", "Old Game", "player_points", 10.5, start="2026-11-03T00:00:00+00:00")
        + _row("2026-11-04T22:00:00+00:00", "Old Game", "player_points", 14.5, start="2026-11-03T00:00:00+00:00"))
    got = propmoves.moves(tmp_path, NOW)
    assert [(m["player"], m["side"], m["market"]) for m in got] == [
        ("Cade Cunningham", "over", "assists"), ("Joel Embiid", "under", "points")]  # unmoved and started games left out
