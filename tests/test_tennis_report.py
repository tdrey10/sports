"""Tennis player report (owner, Oct 25): form, surface, fatigue, health, head to head."""

from datetime import datetime, timedelta, timezone

from sportsedge import tennisreport

NOW = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)


def row(i, p1, p2, winner, days_ago, status="STATUS_FINAL", surface="hard", sets=("6 6", "3 4")):
    return {"id": str(i), "tour": "wta", "start": (NOW - timedelta(days=days_ago)).isoformat(), "status": status,
            "winner": winner, "p1_name": p1, "p2_name": p2, "sets1": sets[0], "sets2": sets[1], "surface": surface,
            "bo": "3", "tourney": "Test Open", "round": "R1", "event": "e1"}


def test_report_flags_fatigue_health_form_and_head_to_head():
    rows = []
    i = 0
    for d in range(60, 20, -2):  # Busy Bee: 20 matches, wins the early ones, loses the last 10
        i += 1
        rows.append(row(i, "Busy Bee", f"Opp {i}", "1" if d > 40 else "2", d))
    for d in (2.5, 1.5, 0.5):  # and three matches in the last three days
        i += 1
        rows.append(row(i, "Busy Bee", f"Opp {i}", "1", d))
    i += 1
    rows.append(row(i, "Fresh Face", "Busy Bee", "1", 200))  # their only meeting
    i += 1
    rows.append(row(i, "Fresh Face", "Someone", "2", 10, status="STATUS_RETIRED"))  # retired, 10 days ago
    rows.append({**row(i + 1, "Busy Bee", "Fresh Face", "", -0.25), "status": "STATUS_SCHEDULED"})
    rep = tennisreport.build(rows, NOW)
    m = rep["matches"][0]
    bee, fresh = m["a"], m["b"]
    assert bee["load3"] == 3 and any("3 matches in the last 3 days" in n for n in m["notes"])
    assert any("retired from 1 match" in h for h in fresh["health"]) and fresh in rep["hurt"]
    assert m["h2h"] == "Busy Bee 0-1"
    assert bee["last10"] == "3-7" and bee["streak"] == "W3"
