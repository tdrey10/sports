"""Owner, Oct 25: a probable player counts as playing (ESPN's 'Day-To-Day' plus the written call)."""

from sportsedge.data.base import is_out
from sportsedge.data.espn import parse_injuries, refine_status


def test_the_written_call_decides_day_to_day():
    assert refine_status("Day-To-Day", "Embiid (knee) is listed as probable for Sunday's game.") == "Probable"
    assert refine_status("Day-To-Day", "He was upgraded from questionable to probable.") == "Probable"
    assert refine_status("Day-To-Day", "He was downgraded from probable to questionable.") == "Questionable"
    assert refine_status("Day-To-Day", "Coach said he's a game-time decision.") == "Questionable"
    assert refine_status("Day-To-Day", "He has been ruled out for Sunday.") == "Out"
    assert refine_status("Day-To-Day", "Listed as doubtful.") == "Doubtful" and is_out("Doubtful")
    assert refine_status("Day-To-Day", "") == "Day-To-Day"
    assert refine_status("Out", "expected to play next week") == "Out"  # an explicit status stays


def test_probable_is_not_questionable_in_the_engine():
    data = {"injuries": [{"displayName": "Philadelphia 76ers", "injuries": [
        {"athlete": {"displayName": "Joel Embiid", "position": {"abbreviation": "C"}}, "status": "Day-To-Day",
         "shortComment": "Embiid (knee) is listed as probable for Sunday's game against the Pistons."},
        {"athlete": {"displayName": "Kelly Oubre Jr.", "position": {"abbreviation": "F"}}, "status": "Day-To-Day",
         "shortComment": "Oubre is questionable for Sunday."}]}]}
    rows = {n: s for n, s, _ in parse_injuries(data)["Philadelphia 76ers"]}
    assert rows == {"Joel Embiid": "Probable", "Kelly Oubre Jr.": "Questionable"}
    questionable = [n for n, s in rows.items() if "question" in s.lower() or "day-to-day" in s.lower()]
    assert questionable == ["Kelly Oubre Jr."] and not is_out("Probable")
