from sportsedge.rival import head_to_head


def test_head_to_head_grades_both_engines_the_same_way():
    picks = [
        {"date": "2026-10-01", "kind": "lock", "units": 2.0, "dec": 1.5, "status": "won", "legs": [{}]},
        {"date": "2026-10-02", "kind": "dog", "units": 1.0, "dec": 2.5, "status": "lost", "legs": [{}]},
        {"date": "2026-10-02", "kind": "lean", "units": 0, "dec": 1.9, "status": "won", "legs": [{}]},
        {"date": "2026-10-02", "kind": "three", "units": 0, "dec": 6.0, "status": "lost", "legs": [{}, {}, {}]},
        {"date": "2026-10-03", "kind": "play", "units": 0.5, "dec": 2.1, "status": "open",
         "american": 110, "legs": [{"league": "ncaaf", "team": "Rice", "market": "ml", "edge": 0.01}]},
    ]
    ours = [
        {"day": "2026-09-30", "units": 1.0, "status": "won", "profit": 0.9},  # before his first pick: left out
        {"day": "2026-10-01", "units": 0.5, "status": "lost", "profit": -0.5},
        {"day": "2026-10-02", "units": 0.0, "status": "won", "profit": 0.91},
        {"day": "2026-10-03", "units": 1.0, "status": "pending", "profit": None},
    ]
    r = head_to_head(picks, ours, "2026-10-03")
    assert r["since"] == "2026-10-01"
    t, o = r["theirs"], r["ours"]
    assert (t["money"]["won"], t["money"]["lost"], t["money"]["units"], t["money"]["staked"]) == (1, 1, 0.0, 3.0)
    assert (t["leans"]["won"], t["leans"]["units"]) == (1, 0.9)  # the parlay is left out
    assert (o["money"]["lost"], o["money"]["units"], o["leans"]["won"]) == (1, -0.5, 1)
    assert [p["legs"][0]["team"] for p in r["today"]] == ["Rice"]
    assert head_to_head([], ours, "2026-10-03") is None
