"""Game-factor study (owner, Oct 25): every situation that can swing a game, tested blind, on every game."""

from datetime import datetime, timedelta, timezone

from sportsedge import factorstudy as fs
from sportsedge.data.espn import parse_illness
from sportsedge.linestudies import G

T0 = datetime(2025, 11, 1, 0, 0, tzinfo=timezone.utc)


def game(day, home, away, hs, as_, sport="nba", season=2025, ml=(-150, 130), spread=-3.5, gid=None, **kw):
    v = {"tzo": -5.0, "elev": 100.0, "temp": None, "wind": None, "rain": None, "indoor": True}
    v.update(kw)
    return G(sport, gid or f"{home}{away}{day}", season, T0 + timedelta(days=day), home, away, hs, as_, False,
             {"home": ml[0], "away": ml[1]}, {}, spread, {"home": -110.0, "away": -110.0}, None, {}, {}, v["elev"],
             v["tzo"], v["temp"], v["wind"], v["rain"], v["indoor"], {"home": "", "away": ""}, "2", {}, None, {}, {})


def test_schedule_form_and_revenge_are_read_before_the_game():
    tr = fs.Tracker("nba")
    for g in (game(0, "A", "B", 90, 100), game(2, "C", "A", 120, 95), game(3, "D", "A", 110, 100)):
        tr._new_season(g.season)
        tr.add(g)
    c = tr.ctx("A", "B", "away", T0 + timedelta(days=4), False)
    assert c["rest"] == 1 and c["in4"] == 2 and c["streak"] == -3 and c["road_run"] == 2
    assert c["last_margin"] == -10 and c["revenge"] is True  # B beat A on day 0
    acts = {n: d for d, n in fs.active("nba", c, tr.ctx("B", "A", "home", T0 + timedelta(days=4), False))}
    assert acts["on a back-to-back"] == "back" and acts["3rd game in 4 days"] == "back"
    assert acts["on a 3+ game losing streak"] == "back" and acts["3rd+ straight road game"] == "back"
    assert acts["2+ more days of rest than the other team"] == "fade"  # B rested 4 days, A one
    assert tr.ctx("B", "A", "home", T0 + timedelta(days=4), False)["revenge"] is False


def test_a_team_that_keeps_beating_the_line_on_short_rest_is_flagged():
    tr = fs.Tracker("nba")
    day = 0
    for i in range(fs.SPLIT_MIN):  # A plays two nights running and covers the second every time
        for g in (game(day, "A", f"X{i}", 100, 99), game(day + 1, "A", f"Y{i}", 110, 100, spread=-2)):
            tr._new_season(g.season)
            tr.add(g)
        day += 4
    c = tr.ctx("A", "Z", "home", T0 + timedelta(days=day - 2), False)
    assert c["rest"] <= 1.5 and c["own"]["on short rest"][0] >= fs.SPLIT_EDGE
    assert ("back", "this team thrives on short rest (its own history)") in fs.active("nba", c, tr.ctx("Z", "A", "away", T0 + timedelta(days=day - 2), False))


def test_hoops_never_counts_as_outdoors_and_football_weather_is_game_wide():
    tr = fs.Tracker("nba")
    assert "outdoors" not in tr.ctx("A", "B", "away", T0, False, indoor=False)["spots"]
    f = fs.Tracker("nfl")
    me = f.ctx("A", "B", "away", T0, False, indoor=False, wind=20)
    op = f.ctx("B", "A", "home", T0, False, indoor=False, wind=20)
    acts = fs.active("nfl", me, op)
    assert ("back", "windy game (15+ mph)") in acts and ("fade", "windy game (15+ mph)") not in acts


def test_study_keeps_tuning_and_blind_apart_and_runs_the_luck_control():
    games = []
    for s in (2019, 2023):
        for i in range(40):
            games.append(game(i * 2 + (s - 2019) * 400, "A", "B", 100 + (i % 3), 98, season=s, gid=f"{s}-{i}"))
    recs, control, _ = fs.study(games, "nba", n_random=5)
    row = recs[("spread", "back: on a 3+ game win streak")]
    assert row["in_sample"].bets > 0 and row["blind"].bets > 0
    assert len({k[1] for k in control}) == 5


def test_game_study_notes_put_the_proven_spot_first():
    rows = [{"team": "Wolves", "factor": "on a back-to-back", "best": "none",
             "markets": [{"market": "spread", "bet": "back", "tag": "none", "text": "no edge: 90 blind bets, -2.0%"}]},
            {"team": "Wolves", "factor": "off a blowout win (20+)", "best": "edge",
             "markets": [{"market": "moneyline favorite", "bet": "back", "tag": "edge", "text": "PROVEN money spot: 194 blind bets, +9.4%"}]}]
    lines = fs.notes(rows)
    assert lines[0].startswith("🧪 Wolves off a blowout win (20+): back them as a favorite — PROVEN money spot")
    assert lines[1].endswith("tested, no edge either way")


def test_sick_players_come_off_the_injury_report():
    data = {"injuries": [{"displayName": "Philadelphia 76ers", "injuries": [
        {"athlete": {"displayName": "Joel Embiid"}, "details": {"type": "Illness"}, "status": "Questionable"},
        {"athlete": {"displayName": "Paul George"}, "details": {"type": "Knee"}, "shortComment": "knee soreness"},
        {"athlete": {"displayName": "Kelly Oubre Jr."}, "shortComment": "Oubre is out with the flu."}]}]}
    assert parse_illness(data) == {"Philadelphia 76ers": ["Joel Embiid", "Kelly Oubre Jr."]}
