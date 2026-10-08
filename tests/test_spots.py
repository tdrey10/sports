from datetime import datetime, timedelta, timezone

import requests

from sportsedge.models.ratings import GameResult
from sportsedge.spots import capped_bump, is_dog_price, team_spots, zone

MON_NIGHT = datetime(2026, 10, 13, 0, 15, tzinfo=timezone.utc)  # Monday 8:15 PM Eastern


def g(eid, days_before, home, away, hs, as_, when=MON_NIGHT):
    return GameResult(eid, when - timedelta(days=days_before), home, away, hs, as_)


def test_time_zones():
    assert zone("nfl", "Seattle Seahawks") == "PT" and zone("nfl", "Miami Dolphins") == "ET"
    assert zone("nhl", "Montréal Canadiens") == "ET" and zone("mlb", "Athletics") == "PT"
    assert zone("nba", "Nobody FC") is None


def test_monday_night_bye_travel_and_blowout_spots():
    results = [
        g("a", 15, "Seattle Seahawks", "Dallas Cowboys", 20, 17),  # Seattle off a bye (15 days)
        g("b", 8, "Miami Dolphins", "New York Jets", 35, 10),  # Miami won by 25 last week
    ]
    spots = team_spots("nfl", "Seattle Seahawks", "Miami Dolphins", MON_NIGHT, results)
    assert "mnf_dog" in spots["home"] and "mnf_dog" in spots["away"]
    assert "bye_dog" in spots["home"] and "bye_dog" not in spots["away"]
    assert "blowout_dog" in spots["away"]
    assert "east_traveler_dog" in spots["away"] and "west_host_dog" in spots["home"]
    # Only games before kickoff count: a later game can't leak in.
    later = results + [g("c", -3, "Miami Dolphins", "Buffalo Bills", 0, 40)]
    assert team_spots("nfl", "Seattle Seahawks", "Miami Dolphins", MON_NIGHT, later) == spots


def test_spots_are_sport_specific():
    sunday = MON_NIGHT - timedelta(days=1)
    spots = team_spots("nba", "Seattle Seahawks", "Miami Dolphins", sunday, [])
    assert spots == {"home": [], "away": []}


def test_dog_range_and_caps():
    assert is_dog_price(100) and is_dog_price(220) and not is_dog_price(221) and not is_dog_price(-110)
    assert capped_bump([0.04, 0.05], 150) == 0.06  # dogs: +/-6 points together
    assert capped_bump([0.03, 0.03], -120) == 0.04  # favorites: 4
    assert capped_bump([-0.09], 150) == -0.06


def test_closing_odds_404_means_no_odds_but_errors_retry():
    from sportsedge.data.espn_odds import EspnOdds
    from sportsedge.sports import SPORTS

    class Resp:
        def __init__(self, code):
            self.status_code = code

    class Espn:
        def __init__(self, exc):
            self.exc = exc

        def _get(self, url, params, ttl):
            raise self.exc

    not_found = requests.HTTPError(response=Resp(404))
    assert EspnOdds(Espn(not_found)).closing_quotes(SPORTS["nfl"], "1") == []
    assert EspnOdds(Espn(requests.ConnectionError())).closing_quotes(SPORTS["nfl"], "1") is None


def test_only_proven_spots_add_weight_to_the_read():
    from sportsedge.data.base import Event, Quote
    from sportsedge.edge import Settings
    from sportsedge.engine import Engine
    from sportsedge.models.game import predict_game
    from sportsedge.models.ratings import TeamRatings
    from sportsedge.sports import SPORTS

    cfg = SPORTS["nfl"]
    r = TeamRatings(link="identity", mu=22.0, hfa=0.0, off={"A": -2.0, "B": 2.0}, deff={"A": 0.0, "B": 0.0})
    pred = predict_game(cfg, r, "A", "B", neutral=True)
    ev = Event(id="e", sport="nfl", commence_time=MON_NIGHT, home="A", away="B", quotes=[
        Quote("h2h", "home", None, "draftkings", 160), Quote("h2h", "away", None, "draftkings", -190)])
    spots = {"home": ["mnf_dog"], "away": []}

    def home_prob(weights):
        picks = Engine(None, Settings(spot_weights=weights))._game_picks(cfg, ev, pred, spots)
        return next(p.model_prob for p in picks if p.side == "home")

    base = home_prob({})
    assert abs(home_prob({"nfl:mnf_dog": 0.03}) - base - 0.03) < 0.01
    assert abs(home_prob({"nfl:mnf_dog": 0.20}) - base - 0.06) < 0.01  # capped at 6 points on a dog
    assert home_prob({"nba:mnf_dog": 0.05}) == base  # other sports' weights don't leak in


def test_steam_dog_detection_and_study(tmp_path):
    from sportsedge import archive
    from sportsedge.data.base import Quote
    from sportsedge.sports import SPORTS
    from sportsedge.spots import steamed
    from sportsedge.study import steam_bets

    assert steamed(200, 160)  # 33% -> 38%: money came in on the dog
    assert not steamed(200, 190)  # small move
    assert steamed(260, 120)  # a big move, still a dog in range
    assert not steamed(160, -110)  # now a favorite: out of the +100..+220 range

    start = MON_NIGHT
    folder = tmp_path / "odds"
    (folder / "nfl").mkdir(parents=True)
    rows = ["seen_at,event_id,start,home,away,market,side,point,book,price"]
    for seen, home_price in (("2026-10-10T14:00:00+00:00", 200), ("2026-10-11T14:00:00+00:00", 160),
                             ("2026-10-12T14:00:00+00:00", 150)):
        rows.append(f"{seen},g1,{start.isoformat()},A,B,h2h,home,,draftkings,{home_price}")
        rows.append(f"{seen},g1,{start.isoformat()},A,B,h2h,away,,draftkings,-240")
    (folder / "nfl" / "2026-10.csv").write_text("\n".join(rows) + "\n")
    hist = archive.moneyline_history(folder, "nfl")
    assert [p for _, p in hist["g1"]["home"]] == [200.0, 160.0, 150.0]

    game = GameResult("g1", start, "A", "B", 24, 20)
    close = [Quote("h2h", "home", None, "draftkings", 140), Quote("h2h", "away", None, "draftkings", -165)]
    bets = steam_bets(SPORTS["nfl"], [game], lambda g: close, folder, start - timedelta(days=5), start + timedelta(days=1))
    got = {(at, b.american, b.win) for k, at, b in bets}
    assert got == {("open", 160.0, 1.0), ("close", 140.0, 1.0)}  # bet when the move showed up, and after it
