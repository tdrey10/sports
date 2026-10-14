import gzip
import json

from sportsedge import shopstudy


def test_best_price_against_the_other_books_consensus():
    books = {f"b{i}": [-150, 130] for i in range(6)}
    books["soft"] = [-150, 150]  # the away side at +150 vs a consensus near 40.4%
    pick = shopstudy.best_bet({"books": books}, 0.02)
    assert pick and pick[1] is False and pick[2] == 150 and 0.0 < pick[0] < 0.15
    assert shopstudy.best_bet({"books": {f"b{i}": [-150, 130] for i in range(6)}}, 0.02) is None


def test_run_grades_snapshots_on_results(tmp_path):
    (tmp_path / "results").mkdir()
    rows, lines = [], []
    for i in range(80):
        season = 2021 if i < 40 else 2024
        start = f"{season}-10-{i % 28 + 1:02d}T18:00Z"
        rows.append(f"g{i},nfl,{start},final,1,2,Home{i},Away{i},{20 if i % 3 else 10},17")
        books = {f"b{k}": [-150, 130] for k in range(6)}
        books["soft"] = [-150, 150]
        lines.append(json.dumps({"snap": start.replace("18:00Z", "10:00Z").replace(f"-{i % 28 + 1:02d}T", f"-{i % 28 + 1:02d}T"),
                                 "id": f"e{i}", "t": start, "h": f"Home{i} Hawks", "a": f"Away{i} Owls", "b": books}))
    (tmp_path / "results" / "nfl-2021-10.csv").write_text(
        "id,league,start,status,home,away,home_name,away_name,home_score,away_score\n" + "\n".join(rows))
    with gzip.open(tmp_path / "americanfootball_nfl.jsonl.gz", "wt") as fh:
        fh.write("\n".join(lines))
    with gzip.open(tmp_path / "americanfootball_ncaaf.jsonl.gz", "wt") as fh:
        fh.write("")
    res = shopstudy.run(tmp_path, random_n=6)
    nfl = [r for r in res["rows"] if r["sport"] == "nfl" and r["hours"] == 1 and r["edge"] == 0.02][0]
    assert nfl["in_sample"]["bets"] == 40 and nfl["blind"]["bets"] == 40
    assert "luck control" in shopstudy.report(res)
