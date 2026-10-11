from datetime import datetime, timedelta, timezone

from sportsedge import nflhist, teasers
from sportsedge.ledger import Ledger
from sportsedge.models.ratings import GameResult

NOW = datetime(2026, 10, 11, 12, tzinfo=timezone.utc)
STUDY = {"teasers": {"wong_dog": {"proven": True, "blind": {"rate": 0.772, "legs": 381}}}}


def _leg(i, side="away", point=2.5, low=True, cover=0.8):
    t = NOW + timedelta(hours=5)
    return {"event_id": f"e{i}", "team": f"T{i}", "side": side, "home": f"H{i}", "away": f"A{i}",
            "commence_time": t.isoformat(), "point": point, "teased": point + 6, "total": 44.5,
            "low_total": low, "model_cover": cover, "status": "pending", "final": None}


def test_teaser_legs_study_finds_small_dogs_and_flags_proven():
    def g(season, spread, margin, total=44.0):
        return nflhist.Game(season, 3, None, "Sunday", "H", "A", 20 + margin, 20, False, 7, 7, None, None,
                            spread, None, None, total, None, None, "", "")

    games = [g(2010, -2.0, -5) for _ in range(80)] + [g(2020, -2.0, -5) for _ in range(80)]  # home dog +2 loses by 5: covers +8
    res = nflhist.teaser_legs(games, 2018)
    assert res["wong_dog"]["blind"]["rate"] == 1.0 and res["wong_dog"]["proven"]
    assert "wong_dog_low_total" in res
    assert nflhist.teaser_kind(2.5, 44) == ["wong_dog", "wong_dog_low_total"] and nflhist.teaser_kind(3.0, 44) == []


def test_builds_one_2_and_3_team_a_day_without_reusing_legs(monkeypatch):
    legs = [_leg(i) for i in range(4)]
    monkeypatch.setattr(teasers, "candidate_legs", lambda scans, now: [dict(x) for x in legs])
    made = teasers.build([], [], STUDY, NOW, "2026-10-11")
    assert [t.size for t in made] == [2, 3] and made[0].units == 1.0 and made[1].units == 0.5
    assert teasers.build([], made, STUDY, NOW, "2026-10-11") == []  # once a day
    later = teasers.build([], made, STUDY, NOW, "2026-10-12")
    assert later == []  # every leg is already in an open teaser
    assert teasers.build([], [], None, NOW, "2026-10-11") == []  # no study: no teaser
    unproven = {"teasers": {"wong_dog": {"proven": False, "blind": {"rate": 0.75}}}}
    assert all(t.units == 0 for t in teasers.build([], [], unproven, NOW, "2026-10-11"))


def test_grading_leg_by_leg_with_a_push_dropping_out(tmp_path):
    t = teasers.Teaser("x", "2026-10-11", 3, 160.0, 0.5, [_leg(0), _leg(1), _leg(2)], NOW.isoformat(), 0.77, True)
    start = NOW + timedelta(hours=5)
    games = [GameResult("g0", start, "H0", "A0", 24, 20, state="post"),   # A0 +8.5 covers (lost by 4)
             GameResult("g1", start, "H1", "A1", 28, 20, state="post"),   # A1 +8.5 covers (lost by 8)
             GameResult("g2", start, "H2", "A2", 27, 20, state="post")]   # A2 +8.5 covers (lost by 7)
    assert teasers.grade([t], lambda sport, s, e: games, start + timedelta(hours=4)) == 1
    assert t.status == "won" and abs(t.profit - 0.5 * 1.6) < 1e-9
    lose = teasers.Teaser("y", "2026-10-11", 2, -120.0, 1.0, [_leg(0, point=1.5), _leg(1, point=1.5)], NOW.isoformat())
    games2 = [GameResult("g0", start, "H0", "A0", 30, 20, state="post"), GameResult("g1", start, "H1", "A1", 27, 20, state="post")]
    teasers.grade([lose], lambda sport, s, e: games2, start + timedelta(hours=4))
    assert lose.status == "lost" and lose.legs[0]["status"] == "lost" and lose.legs[1]["status"] == "won"
    push = teasers.Teaser("z", "2026-10-11", 2, -120.0, 1.0, [_leg(0, point=2.0), _leg(1, point=1.5)], NOW.isoformat())
    games3 = [GameResult("g0", start, "H0", "A0", 28, 20, state="post"),  # A0 +8 vs lost by 8: push, leg drops out
              GameResult("g1", start, "H1", "A1", 20, 20, state="post")]  # A1 +7.5: covers
    teasers.grade([push], lambda sport, s, e: games3, start + timedelta(hours=4))
    assert push.legs[0]["status"] == "push" and push.status == "push" and push.profit == 0.0  # one leg left: no action
    L = Ledger()
    L.teasers = [t, lose, push]
    L.save(tmp_path / "l.json")
    assert {x.id: x.status for x in Ledger.load(tmp_path / "l.json").teasers} == {"x": "won", "y": "lost", "z": "push"}
    assert teasers.record([t, lose, push]) == {"2": {"won": 0, "lost": 1, "units": -1.0}, "3": {"won": 1, "lost": 0, "units": 0.8}}
