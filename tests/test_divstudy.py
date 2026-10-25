from sportsedge import divstudy as D

HEAD = ("game_id,season,game_type,away_team,away_score,home_team,home_score,away_moneyline,home_moneyline,"
        "spread_line,away_spread_odds,home_spread_odds,total_line,under_odds,over_odds,div_game\n")


def _file(tmp_path, rows):
    (tmp_path / "2023.csv").write_text(HEAD + "\n".join(rows) + "\n")
    return tmp_path


def test_div_dog_wins_and_covers(tmp_path):
    # home favored by 3 (line +3), away dog +130 wins outright 20-17; total 40 goes over
    folder = _file(tmp_path, ["g1,2023,REG,A,20,H,17,130,-150,3,-110,-110,40,-110,-110,1"])
    got = {label: (p, r) for label, p, r in D.bets(D.load(folder)[0])}
    assert got["divisional dog moneyline"] == (130.0, 1.0)
    assert got["divisional lock favorite moneyline"] == (-150.0, 0.0)
    assert got["divisional dog spread"][1] == 1.0
    assert got["divisional favorite spread"][1] == 0.0
    assert got["divisional over"][1] == 0.0 and got["divisional under"][1] == 1.0  # 37 points vs 40


def test_non_div_and_playoffs(tmp_path):
    folder = _file(tmp_path, ["g1,2023,REG,A,20,H,17,130,-150,3,-110,-110,37,-110,-110,0",
                              "g2,2023,POST,A,20,H,17,130,-150,3,-110,-110,37,-110,-110,1"])
    games = D.load(folder)
    assert len(games) == 1  # playoffs left out
    labels = {b[0] for b in D.bets(games[0])}
    assert "non-divisional over" in labels and not any(l.startswith("divisional") for l in labels)
    assert dict((b[0], b[2]) for b in D.bets(games[0]))["non-divisional under"] == 0.5  # push on 37


def test_run_with_no_data_is_empty(tmp_path):
    assert D.run(tmp_path)["rows"] == []
