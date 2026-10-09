from datetime import datetime, timedelta, timezone

from sportsedge.advanced import Advanced, game_metrics, parse_summary
from sportsedge.models.ratings import GameResult
from sportsedge.sports import SPORTS


def nba_team(tid, where, fg, tp, ft, oreb, dreb, tov):
    return {"team": {"id": tid}, "homeAway": where, "statistics": [
        {"name": "fieldGoalsMade-fieldGoalsAttempted", "displayValue": fg},
        {"name": "threePointFieldGoalsMade-threePointFieldGoalsAttempted", "displayValue": tp},
        {"name": "freeThrowsMade-freeThrowsAttempted", "displayValue": ft},
        {"name": "offensiveRebounds", "displayValue": str(oreb)},
        {"name": "defensiveRebounds", "displayValue": str(dreb)},
        {"name": "totalTurnovers", "displayValue": str(tov)},
    ]}


SUMMARY = {
    "boxscore": {"teams": [nba_team("1", "away", "32-77", "9-34", "16-22", 10, 31, 22),
                           nba_team("2", "home", "40-85", "14-35", "18-20", 12, 35, 12)]},
    "header": {"competitions": [{"competitors": [
        {"team": {"id": "1"}, "homeAway": "away", "score": "89"},
        {"team": {"id": "2"}, "homeAway": "home", "score": "112"}]}]},
}


def test_box_score_becomes_efficiency_and_pace():
    box = parse_summary(SUMMARY)
    assert box["away"]["fgm"] == 32 and box["away"]["fga"] == 77 and box["home"]["pts"] == 112
    m = game_metrics("basketball", box["home"], box["away"])
    assert m["net"] > 20 and m["efg"] > 0 and m["tov"] > 0 and 90 < m["total"] < 100


def test_rolling_stats_use_only_earlier_games():
    cfg = SPORTS["nba"]
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    games, box = [], {}
    for i in range(6):
        g = GameResult(f"e{i}", t0 + timedelta(days=i), "Home Team", "Away Team", 112, 89, state="post")
        games.append(g)
        box[g.event_id] = parse_summary(SUMMARY)
    adv = Advanced(cfg, games, box)
    assert adv.context(games[2])["adv_known"] == 0.0  # only 2 earlier games: not enough
    ctx = adv.context(games[5])
    assert ctx["adv_known"] == 1.0 and ctx["adv"][0] > 40  # home's net rating minus away's
