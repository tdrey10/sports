"""Soccer shot-luck study (Nov 21, dead: 0 of 25 passed) and the card fact it left behind:
shots on target per game from the saved box scores, from earlier matches only."""

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from sportsedge import advanced, shotluckstudy
from sportsedge.engine import EventContext
from sportsedge.insights import matchup_facts
from sportsedge.models.ratings import GameResult
from sportsedge.sports import resolve
from tests.test_soccerstudy import _write


def _box(folder, boxes):
    (folder / "epl").mkdir(parents=True, exist_ok=True)
    (folder / "epl" / "2025-09.json").write_text(json.dumps(boxes))


def test_study_features_use_only_earlier_matches(tmp_path):
    rows, boxes = [], {}
    for i in range(8):  # Shooter out-shoots Wall every time but they draw 0-0
        rows.append({"event_id": i, "date": f"2025-09-{i + 1:02d}T15:00:00+00:00", "home": "Shooter", "away": "Wall",
                     "home_score": 0, "away_score": 0, "neutral": 0, "close_home_ml": 120, "close_away_ml": 200,
                     "close_draw_ml": 220, "close_total": 2.5, "close_over_price": -110, "close_under_price": -110})
        boxes[str(i)] = {"date": rows[-1]["date"], "home": {"sot": 6.0 + i, "shots": 15.0, "poss_pct": 60.0},
                         "away": {"sot": 1.0, "shots": 4.0, "poss_pct": 40.0}}
    _write(tmp_path / "g", rows)
    _box(tmp_path / "b", boxes)
    games = shotluckstudy.load(tmp_path / "g", tmp_path / "b")
    assert all(g["box"] for g in games)
    shotluckstudy.features(games, 0.3)
    assert games[4]["shot"]["home"] is None  # only 4 earlier matches
    f = games[5]["shot"]["home"]  # matches 0-4: SoT 6..10 for, 1 against, no goals
    assert abs(f["vol"] - (8.0 + 1.0)) < 1e-9 and f["luck"] < 0 and abs(f["share"] - 8 / 9) < 1e-9
    res = shotluckstudy.run(tmp_path / "g", tmp_path / "b", random_n=5)
    assert res["priced"] == 8 and "luck alone gives" in shotluckstudy.report(res)


def test_shot_form_is_from_earlier_boxed_games_only():
    t0 = datetime(2026, 11, 1, tzinfo=timezone.utc)
    games = [GameResult(str(i), t0 + timedelta(days=i), "A", "B", 1, 0) for i in range(6)]
    box = {str(i): {"home": {"sot": float(i)}, "away": {"sot": 2.0}} for i in range(5)}
    box["1"] = {"none": True}  # a missing box score is skipped, not counted as zero
    assert advanced.shot_form(box, games, "A", t0 + timedelta(days=2)) is None  # one boxed game before
    f = advanced.shot_form(box, games, "A", t0 + timedelta(days=5))  # games 0, 2, 3, 4 (5 not played yet)
    assert f == ((0 + 2 + 3 + 4) / 4, 2.0, 4)
    assert advanced.shot_form(box, games, "B", t0 + timedelta(days=5)) == (2.0, (0 + 2 + 3 + 4) / 4, 4)


def test_attach_shot_form_only_for_soccer_and_shows_on_the_card(tmp_path):
    t0 = datetime(2026, 11, 1, tzinfo=timezone.utc)
    games = [GameResult(str(i), t0 + timedelta(days=i), "A", "B", 1, 0) for i in range(4)]
    _box(tmp_path, {str(i): {"home": {"sot": 5.0}, "away": {"sot": 3.0}} for i in range(4)})
    (tmp_path / "nba").mkdir()
    (tmp_path / "nba" / "2026-11.json").write_text((tmp_path / "epl" / "2025-09.json").read_text())

    def scan(key):
        ctx = SimpleNamespace(home="A", away="B", event=SimpleNamespace(commence_time=t0 + timedelta(days=9)),
                              shot_form={})
        return SimpleNamespace(sport=resolve(key)[0], results=games, contexts={"e": ctx})

    epl, nba = scan("epl"), scan("nba")
    assert advanced.attach_shot_form([epl, nba], tmp_path) == 2
    assert epl.contexts["e"].shot_form == {"A": (5.0, 3.0, 4), "B": (3.0, 5.0, 4)} and nba.contexts["e"].shot_form == {}
    ctx = EventContext(epl.contexts["e"].event, "A", "B", False, None, None, shot_form=epl.contexts["e"].shot_form)
    text = " | ".join(matchup_facts(games, "A", "B", t0 + timedelta(days=9), "epl", ctx=ctx))
    assert "Shots on target per game: B 3.0 for / 5.0 against (last 4); A 5.0 for / 3.0 against (last 4)." in text
