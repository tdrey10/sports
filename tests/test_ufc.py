import json
from datetime import datetime, timedelta, timezone

from sportsedge.ufc import Fight, Lines, parse_card, parse_fight_odds, study

NOW = datetime(2026, 10, 9, tzinfo=timezone.utc)

CARD = {"id": "600061541", "name": "UFC Fight Night: Allen vs. Duncan", "competitions": [{
    "id": "401924511", "date": "2026-10-10T21:00Z", "type": {"abbreviation": "W Flyweight"},
    "status": {"type": {"state": "pre", "completed": False}},
    "competitors": [
        {"id": "4420893", "type": "athlete", "order": 2, "winner": False, "athlete": {"displayName": "Melissa Gatto"},
         "records": [{"type": "total", "summary": "9-3-2"}]},
        {"id": "5149147", "type": "athlete", "order": 1, "winner": False, "athlete": {"displayName": "Ernesta Kareckaite"},
         "records": [{"type": "total", "summary": "6-2-1"}]}]}]}
ODDS = {"provider": {"id": "100"},
        "awayAthleteOdds": {"moneyLine": -115, "open": {"moneyLine": {"american": "-170"}},
                            "athlete": {"$ref": "http://core/mma/athletes/4420893?lang=en"}},
        "homeAthleteOdds": {"moneyLine": -105, "open": {"moneyLine": {"american": "+140"}},
                            "athlete": {"$ref": "http://core/mma/athletes/5149147?lang=en"}}}


def test_parse_card_and_draftkings_odds():
    (f,) = parse_card(CARD)
    assert (f.a_name, f.b_name, f.weight, f.a_record) == ("Ernesta Kareckaite", "Melissa Gatto", "W Flyweight", "6-2-1")
    odds = parse_fight_odds(ODDS, f)
    assert odds == {"4420893": {"ml": -115, "open": -170.0}, "5149147": {"ml": -105, "open": 140.0}}


def fight(i, a, b, winner, days_ago):
    d = (NOW - timedelta(days=days_ago)).isoformat()
    return Fight("c", str(i), "card", d, "LW", a, a, "", b, b, "", state="post", winner=winner)


def test_ratings_learn_and_predict_before_each_fight(tmp_path):
    fights = [fight(i, "A", f"X{i}", "A", 100 - i) for i in range(6)]  # A wins six straight
    upcoming = Fight("c", "99", "card", (NOW + timedelta(days=1)).isoformat(), "LW", "A", "A", "6-0", "B", "B", "0-0", state="pre")
    lines = Lines(tmp_path)
    lines.save(upcoming, {"A": {"ml": -130, "open": -120}, "B": {"ml": 110, "open": None}}, NOW)
    out = study(fights + [upcoming], lines.load_all(), NOW)
    assert out["fights"] == 6 and out["called"] == 5 and out["accuracy"] == 1.0  # first fight: two debutants, no call
    row = out["upcoming"][0]
    assert row["model_a"] > 0.6 and row["a_fights"] == 6 and abs(row["market_a"] - 0.541) < 0.01
    assert out["market"]["bets"] == 0 and not out["market"]["proven"]
    json.dumps(out)  # saved as JSON


def test_saved_close_is_graded_by_the_blind_test(tmp_path):
    fights = [fight(i, "A", f"X{i}", "A", 100 - i) for i in range(6)]
    last = fight(7, "A", "B", "A", 1)
    lines = Lines(tmp_path)
    lines.save(last, {"A": {"ml": 120, "open": None}, "B": {"ml": -140, "open": None}}, NOW - timedelta(days=2))
    out = study(fights + [last], lines.load_all(), NOW)
    assert out["market"]["bets"] == 1 and out["market"]["won"] == 1 and out["market"]["units"] == 1.2
