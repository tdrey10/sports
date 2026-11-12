"""Upset hangover study (Nov 12) and its live money spot: a dog off a loss as a big favorite."""

import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from sportsedge import factorstudy, proven
from sportsedge import upsetstudy as U
from tests.test_dogstudy import _g
from tests.test_ledger import make_pick


def _game(i, home, away, hs, as_, home_ml, away_ml, sport="nba", season=2024):
    g = _g(i, home, away, hs, as_)
    g.sport, g.season, g.ml = sport, season, {"home": home_ml, "away": away_ml}
    return g


def test_shock_losses_and_wins_come_from_earlier_games_only():
    g0 = _game(0, "A", "B", 90, 100, -250, 200)  # A lost as a -250 favorite, B won as a +200 dog
    g1 = _game(1, "A", "C", 100, 90, -150, 130)
    g2 = _game(2, "B", "A", 90, 95, -110, -110)
    p = U.last_upset([g0, g1, g2], "nba")
    assert p[0] == {"home": None, "away": None}  # the game's own result never leaks in
    assert p[1] == {"home": "shock loss", "away": None}
    assert p[2] == {"home": "shock win", "away": "other"}  # A's last game is now the ordinary win
    assert U.spots(p[1], "away") == ["opponent last game was a shock loss"]
    assert U.spots(p[1], "home") == ["own last game was a shock loss"]


def test_tighter_prices_count_in_mlb_and_each_season_starts_clean():
    g0 = _game(0, "A", "B", 1, 3, -175, 155, sport="mlb")
    g1 = _game(1, "A", "B", 1, 3, -160, 140, sport="mlb")
    assert U.last_upset([g0, g1], "mlb")[1]["home"] == "shock loss"  # -175 is big in MLB
    assert U.last_upset([g0, g1], "nba")[1]["home"] == "other"  # but not in the NBA
    g2 = _game(2, "A", "B", 1, 3, -160, 140, sport="mlb", season=2025)
    assert U.last_upset([g0, g2], "mlb")[1]["home"] is None


def test_current_reads_each_teams_latest_game_and_drops_stale_ones():
    g0 = _game(0, "A", "B", 90, 100, -250, 200)  # 2023-01-01
    g1 = _game(1, "C", "D", 90, 100, -250, 200)
    now = g1.start + timedelta(days=2)
    assert U.current([g0, g1], "nba", now) == {"C": "shock loss", "D": "shock win", "A": "shock loss", "B": "shock win"}
    later = g1.start + timedelta(days=7.5)
    assert U.current([g0, g1], "nba", later) == {"C": "shock loss", "D": "shock win"}  # A/B played 8.5 days ago, C/D 7.5
    g2 = _game(2, "A", "E", 100, 90, -150, 130)
    assert "A" not in U.current([g0, g1, g2], "nba", g2.start + timedelta(hours=3))  # a normal win since
    assert U.run(__import__("pathlib").Path("/nonexistent"))["rows"] == []


ROW = {"study": "40 upset hangover", "variant": "dog ML: own last game was a shock loss", "sport": "nba",
       "verdict": "PROVEN", "in_sample": {"bets": 170, "flat_roi": 0.038, "se": 0.1},
       "blind": {"bets": 166, "flat_roi": 0.232, "se": 0.098}}


def test_the_spot_gets_money_only_while_it_beats_luck(tmp_path):
    (tmp_path / "line_studies.json").write_text(json.dumps({"rows": [
        ROW, {**ROW, "sport": "nhl", "blind": {"bets": 155, "flat_roi": 0.108, "se": 0.093}}]}))
    got = proven.passed(tmp_path)
    assert [(r["sport"], r["market"]) for r in got] == [("nba", "moneyline dog")]  # NHL's z of 1.16 is luck-sized


def _board(now, home_ml, away_ml):
    start = now + timedelta(hours=6)
    picks = [replace(make_pick(market="h2h", side=s, point=None, american=a, sport="nba", event_id="g1"),
                     commence_time=start) for s, a in (("home", home_ml), ("away", away_ml))]
    ctx = SimpleNamespace(home="Lakers", away="Celtics", neutral=False, injuries_unknown=[], wind_mph=None,
                          event=SimpleNamespace(commence_time=start))
    return SimpleNamespace(sport=SimpleNamespace(key="nba", name="NBA"), picks=picks, contexts={"g1": ctx})


def test_a_dog_off_a_shock_loss_posts_at_half_a_unit_inside_the_tested_prices(monkeypatch, tmp_path):
    (tmp_path / "line_studies.json").write_text(json.dumps({"rows": [ROW]}))
    now = datetime(2026, 11, 12, 15, tzinfo=timezone.utc)
    last = _game(0, "13", "2", 101, 110, -260, 210, season=2026)  # Lakers (13) lost at -260 two days ago
    last.start = now - timedelta(days=2)
    monkeypatch.setattr(factorstudy, "history", lambda sport, root: ([last], {"Lakers": "13", "Celtics": "2"}))
    [c] = proven.find([_board(now, 150, -175)], now, tmp_path)
    assert (c.pick.side, c.units, c.tier) == ("home", 0.5, "value") and c.proven.startswith("PROVEN: 40 upset")
    assert "upset as a big favorite" in c.reasons[0]
    assert proven.find([_board(now, 240, -300)], now, tmp_path) == []  # +240 is past the tested +220
    assert proven.find([_board(now, -120, 100)], now, tmp_path) == []  # the Celtics won as a dog: not this spot
