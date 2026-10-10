"""Data audit: what the archive actually holds, per sport, before any model is trusted.

The engine is only as good as its data, so this answers the questions that decide
whether a sport can be tested at all:

- how many finished games, and how many have a closing line (the price every
  backtest and CLV check is judged against), split by season;
- how many have an opening line too (line movement);
- how many line snapshots were logged (open -> midweek -> close);
- how many prop lines were logged, and how many carry a real book price rather
  than an assumed one;
- how many box scores are saved.

Each sport gets a verdict. Blind testing on an unseen season needs closing lines in
at least two seasons; a single season can only be split in time (tune on the early
months, test on the late ones), which is weaker evidence.
"""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

# Month each season starts in: games before it belong to the previous season.
SEASON_START = {"nfl": 8, "ncaaf": 8, "nba": 10, "ncaab": 10, "nhl": 9, "mlb": 2}
SOCCER_START = 7
MIN_TESTABLE = 300  # closing lines needed before a sport is worth testing at all
MIN_SEASON_LINES = 200  # closing lines for a season to count as a test season
ESTIMATED_BOOKS = ("_est",)  # book keys whose prices are assumed, not quoted


def season(sport: str, when: datetime) -> int:
    start = SEASON_START.get(sport, SOCCER_START)
    return when.year if when.month >= start else when.year - 1


@dataclass
class SportAudit:
    sport: str
    games: int = 0
    closing: int = 0  # games with a closing moneyline (or a closing total, for draw-less gaps)
    closing_total: int = 0
    opening: int = 0
    by_season: dict[int, tuple[int, int]] = field(default_factory=dict)  # season -> (games, with closing line)
    first_line: str | None = None  # first month with closing lines
    snapshot_rows: int = 0
    snapshot_games: int = 0
    snapshot_moved: int = 0  # games whose moneyline was seen at two or more prices
    prop_lines: int = 0
    prop_real_price: int = 0
    prop_graded: int = 0
    box_scores: int = 0

    @property
    def test_seasons(self) -> list[int]:
        return [s for s, (_, lined) in sorted(self.by_season.items()) if lined >= MIN_SEASON_LINES]

    @property
    def verdict(self) -> str:
        if self.closing < MIN_TESTABLE:
            return "too thin to test"
        if len(self.test_seasons) >= 2:
            return "blind test on an unseen season"
        return "one season of lines: time-split test only"


def _rows(folder: Path):
    for f in sorted(folder.glob("*.csv")) if folder.exists() else []:
        with open(f, newline="") as fh:
            yield f.stem, list(csv.DictReader(fh))


def audit_sport(data_dir: Path, sport: str) -> SportAudit:
    data_dir = Path(data_dir)
    a = SportAudit(sport)
    seasons: dict[int, list[int]] = defaultdict(lambda: [0, 0])
    for month, rows in _rows(data_dir / "games" / sport):
        for r in rows:
            a.games += 1
            lined = bool(r.get("close_home_ml") or r.get("close_total"))
            a.closing += bool(r.get("close_home_ml"))
            a.closing_total += bool(r.get("close_total"))
            a.opening += bool(r.get("open_home_ml"))
            s = seasons[season(sport, datetime.fromisoformat(r["date"]))]
            s[0] += 1
            s[1] += lined
            if lined and (a.first_line is None or month < a.first_line):
                a.first_line = month
    a.by_season = {k: (v[0], v[1]) for k, v in seasons.items()}

    prices: dict[tuple[str, str], set[str]] = defaultdict(set)
    for _, rows in _rows(data_dir / "odds" / sport):
        for r in rows:
            a.snapshot_rows += 1
            if r["market"] == "h2h" and r["seen_at"] < r["start"]:
                prices[(r["event_id"], r["side"])].add(r["price"])
    games = {ev for ev, _ in prices}
    a.snapshot_games = len(games)
    a.snapshot_moved = len({ev for (ev, _), seen in prices.items() if len(seen) > 1})

    for _, rows in _rows(data_dir / "props" / sport):
        for r in rows:
            a.prop_lines += 1
            a.prop_real_price += not any(r["book"].endswith(t) for t in ESTIMATED_BOOKS) and bool(r["over_price"])
    for _, rows in _rows(data_dir / "props_graded" / sport):
        a.prop_graded += sum(r["result"] != "void" for r in rows)

    box = data_dir / "boxscores" / sport
    for f in box.glob("*.json") if box.exists() else []:
        try:
            a.box_scores += sum("home" in v for v in json.loads(f.read_text()).values())
        except (OSError, ValueError):
            continue
    return a


def audit(data_dir: Path, sports: list[str] | None = None) -> list[SportAudit]:
    data_dir = Path(data_dir)
    if sports is None:
        sports = sorted(p.name for p in (data_dir / "games").glob("*") if p.is_dir())
    return [audit_sport(data_dir, s) for s in sports]


def _pct(n: int, d: int) -> str:
    return f"{n / d:.0%}" if d else "-"


def report(rows: list[SportAudit]) -> str:
    head = (f"{'sport':11}{'games':>7}{'close':>7}{'%':>5}{'open':>7}{'snap gms':>9}{'moved':>7}"
            f"{'props':>7}{'real $':>7}{'graded':>7}{'box':>6}  verdict")
    out = [head, "-" * len(head)]
    for a in rows:
        out.append(f"{a.sport:11}{a.games:>7}{a.closing:>7}{_pct(a.closing, a.games):>5}{a.opening:>7}"
                   f"{a.snapshot_games:>9}{a.snapshot_moved:>7}{a.prop_lines:>7}{a.prop_real_price:>7}"
                   f"{a.prop_graded:>7}{a.box_scores:>6}  {a.verdict}")
    out.append("")
    out.append("Closing lines by season (lined / games):")
    for a in rows:
        seasons = "  ".join(f"{s}: {l}/{g}" for s, (g, l) in sorted(a.by_season.items()))
        out.append(f"  {a.sport:9} {seasons}   (lines start {a.first_line or 'never'})")
    return "\n".join(out)
