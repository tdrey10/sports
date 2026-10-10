import csv

from sportsedge import espnhist, nflhist

FIELDS = ["id", "status", "start", "home", "away", "home_score", "away_score", "neutral", "indoor", "ml_home",
          "ml_away", "ml_home_open", "ml_away_open", "spread_home", "spread_home_odds", "spread_away_odds", "total",
          "over_odds", "under_odds", "stype", "ls_home", "ls_away", "sp_home", "elev"]


def _src(tmp_path, rows):
    folder = tmp_path / "src"
    folder.mkdir()
    with open(folder / "2024-01.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in FIELDS})
    return folder


def _game(gid, start, home, away, hs, as_, **kw):
    return {"id": f"nba:{gid}", "status": "final", "start": start, "home": home, "away": away,
            "home_score": hs, "away_score": as_, "neutral": "0", "indoor": "1", **kw}


def test_build_converts_lines_rest_and_season(tmp_path):
    src = _src(tmp_path, [
        _game(1, "2023-12-31T00:00Z", "A", "B", 100, 90),
        _game(2, "2024-01-01T00:00Z", "A", "C", 95, 99, ml_home="-150", ml_away="130", ml_home_open="-140",
              ml_away_open="120", spread_home="-3.5", spread_home_odds="-110", spread_away_odds="-110",
              total="220.5", over_odds="-110", under_odds="-110"),
        _game(3, "2024-01-02T00:00Z", "C", "D", 1, 0, ml_home="-1.5", ml_away="130"),  # junk price dropped
        {**_game(4, "2024-01-02T00:00Z", "C", "D", "", ""), "status": "scheduled"},
    ])
    out = tmp_path / "lines"
    assert espnhist.build(src, out, "nba") == 3
    games = {g.gid: g for g in nflhist.load(out)}
    g = games["2"]
    assert g.season == 2023 and g.home_ml == -150 and g.away_ml == 130
    assert g.spread == 3.5  # nflverse sign: home favored by 3.5
    assert g.home_rest == 1 and g.away_rest == 7  # A played yesterday; C's first game (unknown -> default)
    assert g.total == 220.5 and g.under_odds == -110
    assert games["3"].home_ml is None
    assert espnhist.extras(out)["2"]["open_home_ml"] == -140


def test_build_is_byte_for_byte_repeatable(tmp_path):
    src = _src(tmp_path, [_game(1, "2024-01-01T00:00Z", "A", "B", 100, 90, ml_home="-150", ml_away="130")])
    espnhist.build(src, tmp_path / "x", "nba")
    first = (tmp_path / "x" / "2023.csv.gz").read_bytes()
    espnhist.build(src, tmp_path / "x", "nba")
    assert (tmp_path / "x" / "2023.csv.gz").read_bytes() == first


def test_rested_dog_spot_needs_the_other_team_on_a_back_to_back():
    def pred(home_rest, away_rest):
        g = nflhist.Game(2023, 1, None, "Monday", "A", "B", 1, 0, False, home_rest, away_rest,
                         150, -170, None, None, None, None, None, None, "", "")
        return nflhist.Pred(g, 0.0, False, False)

    assert ("dog rested vs a team on a back-to-back", "away") in espnhist.daily_spots(pred(1, 2))
    assert ("dog rested vs a team on a back-to-back", "home") in espnhist.daily_spots(pred(3, 1))
    assert not any("rested" in s for s, _ in espnhist.daily_spots(pred(1, 1)))


def test_preseason_games_are_left_out_and_first_halves_are_summed(tmp_path):
    src = _src(tmp_path, [
        _game(1, "2024-01-01T00:00Z", "A", "B", 100, 90, stype="1"),
        _game(2, "2024-01-02T00:00Z", "A", "B", 100, 90, stype="2", ls_home="20,30,25,25", ls_away="30,20,20,20"),
    ])
    out = tmp_path / "lines"
    assert espnhist.build(src, out, "nba") == 1
    assert espnhist.first_half("mlb", "0,1,0,2,0,3,0,0,0") == 3
    assert espnhist.first_half("ncaab", "41,33") == 41
    assert espnhist.first_half("nba", "20") is None and espnhist.first_half("nhl", "1,2,0") is None
