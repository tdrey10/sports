from sportsedge import juicestudy as J


def test_hold_and_tags():
    assert abs(J.hold(-110, -110) - 0.0476) < 1e-3
    assert J.hold(None, -110) is None
    assert J.juice_tag(-125) == "juiced -120 or worse"
    assert J.juice_tag(105) == "plus money" and J.juice_tag(-110) is None


def test_rows_for_spots_and_control():
    spots, every = J.rows_for("ncaab", 2023, {"home": -150, "away": 130}, {"home": 1.0, "away": 0.0},
                              {"home": -125, "away": 105}, {"over": 0.0, "under": 1.0},
                              {"over": -110, "under": -110}, {"home": 1.0, "away": 0.0}, (0.02, 0.03))
    names = {s[0] for s in spots}
    assert "spread side juiced -120 or worse" in names and "spread side plus money" in names
    assert "lock-price fav ML: high hold" in names and "dog ML: high hold" in names
    assert not any(n.startswith(("over", "under")) for n in names)  # -110 totals aren't shaded
    assert len(every) == 6  # 2 ML + 2 spread + 2 totals


def test_study_splits_tuning_and_blind():
    rows = [(s, {"home": -110, "away": -110}, {}, {}, {"over": 1.0, "under": 0.0},
             {"over": 105, "under": -125}, {"home": 1.0, "away": 0.0}) for s in (2019, 2023)]
    recs, ctl = {}, {}
    J.study("nhl", rows, 2021, recs, ctl, n_random=3)
    assert recs[("nhl", "over plus money")]["in_sample"].bets == 1
    assert recs[("nhl", "over plus money")]["blind"].bets == 1
