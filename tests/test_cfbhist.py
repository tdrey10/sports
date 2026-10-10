from collections import Counter

from sportsedge import cfbhist, nflhist


def _line(gid, book, market, abbr, odds, line="", o_odds="", o_line="", home="1", away="2"):
    return {"game_id": gid, "book": book, "market_type": market, "abbr": abbr, "odds": str(odds), "lines": str(line),
            "opening_odds": str(o_odds), "opening_lines": str(o_line), "home_team_id": home, "away_team_id": away}


def test_prices_that_are_not_real_american_odds_are_dropped():
    assert cfbhist._price("-1.5", "spread") is None  # the spread number sitting in the odds field
    assert cfbhist._price("-2500", "spread") is None
    assert cfbhist._price("-2500", "money_line") == -2500
    assert cfbhist._price("-110", "total") == -110 and cfbhist._price("", "total") is None


def test_median_price_is_taken_on_win_chances():
    # The plain median of -105 and +105 is 0, a fake 100-to-1 payout.
    assert cfbhist._median([-105, 105]) == 100
    assert cfbhist._median([-200, -150, -120]) == -150


def test_a_shared_abbreviation_is_the_team_it_names_most_often():
    lines = [_line(f"g{i}", "B", "money_line", "MIA", -150, home="2390", away=str(100 + i)) for i in range(5)]
    lines += [_line("x", "C", "money_line", "MIA", 120, home="999", away="193")]  # Miami (OH) at another book
    lines += [_line("y", "C", "money_line", "MIA", 120, home="193", away="5")]
    ids = cfbhist.team_ids(lines)
    assert ids["MIA"]["2390"] == 5 and ids["MIA"]["193"] == 2
    assert cfbhist.side_of("MIA", "2390", "77", ids) == "home"
    assert cfbhist.side_of("MIA", "999", "193", ids) == "away"
    assert cfbhist.side_of("ZZZ", "1", "2", ids) is None


def test_game_lines_consensus_pinnacle_best_and_opening():
    rows = [
        _line("g", "PINNACLE", "money_line", "H", -150), _line("g", "PINNACLE", "money_line", "A", 135),
        _line("g", "Book1", "money_line", "H", -160, o_odds=-140), _line("g", "Book1", "money_line", "A", 145, o_odds=120),
        _line("g", "Book2", "money_line", "H", -155), _line("g", "Book2", "money_line", "A", 150),
        _line("g", "Book1", "spread", "H", -110, line=-3.5, o_odds=-110, o_line=-2.5),
        _line("g", "Book1", "spread", "A", -110, line=3.5, o_odds=-110, o_line=2.5),
        _line("g", "Book1", "total", "over", -105, line=50.5), _line("g", "Book1", "total", "under", -115, line=50.5),
    ]
    ids = {"H": Counter({"1": 3}), "A": Counter({"2": 3})}
    g = cfbhist.game_lines(rows, ids)
    assert g["pin_home_ml"] == -150 and g["pin_away_ml"] == 135
    assert g["best_home_ml"] == -155 and g["best_away_ml"] == 150  # Pinnacle left out of the best price
    assert g["home_moneyline"] == -155 and g["books"] == 3
    assert g["spread_line"] == 3.5 and g["open_spread_line"] == 2.5  # home favored by 3.5 (nflverse sign)
    assert g["open_home_ml"] == -140 and g["total_line"] == 50.5 and g["under_odds"] == -115


def test_build_writes_rest_days_and_marks_neutral_and_bowl_games(tmp_path):
    sched = [
        {"game_id": "1", "season": 2015, "week": 1, "postseason": False, "date": "2015-09-05", "home": "A",
         "away": "B", "home_score": 30, "away_score": 20, "neutral": False},
        {"game_id": "2", "season": 2015, "week": 2, "postseason": False, "date": "2015-09-12", "home": "B",
         "away": "C", "home_score": 10, "away_score": 13, "neutral": True},
        {"game_id": "3", "season": 2015, "week": 1, "postseason": True, "date": "2015-12-30", "home": "A",
         "away": "C", "home_score": None, "away_score": None, "neutral": True},  # not played: left out
    ]
    assert cfbhist.build([], sched, tmp_path) == 2
    games = nflhist.load(tmp_path)
    assert [g.gid for g in games] == ["1", "2"]
    assert games[1].home_rest == 7 and games[1].neutral and games[1].home_ml is None


def test_sharp_value_bets_the_better_price_only_inside_the_band():
    g = nflhist.Game(season=2018, week=3, day=__import__("datetime").date(2018, 9, 15), weekday="Saturday",
                     home="A", away="B", home_score=20, away_score=24, neutral=False, home_rest=7, away_rest=7,
                     home_ml=-130, away_ml=110, spread=None, home_spread_odds=None, away_spread_odds=None,
                     total=None, over_odds=None, under_odds=None, home_qb="", away_qb="", gid="g")
    ex = {"g": {"pin_home_ml": -125, "pin_away_ml": 115, "best_home_ml": -120, "best_away_ml": 130}}
    rec = cfbhist.sharp_value([g], ex, blind_from=2017)
    assert rec["blind"].bets == 1 and rec["blind"].won == 1  # away +130 vs a fair ~+120
    ex["g"]["best_away_ml"] = 400  # beyond the long-shot cap
    assert cfbhist.sharp_value([g], ex, blind_from=2017)["blind"].bets == 0
