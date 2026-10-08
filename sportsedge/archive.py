"""Plain CSV archives, one file per league per month, so the data outlives any model.

- data/games/<sport>/<YYYY-MM>.csv: every final score with its closing (and opening) lines.
- data/odds/<sport>/<YYYY-MM>.csv: a snapshot of every game line each time the engine scans,
  so line movement (open, midweek, close) is on record.
- data/props/<sport>/<YYYY-MM>.csv: every player prop line seen, logged daily, because prop
  ideas can't be backtested without a history of the lines.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from .data.base import Quote
from .models.ratings import GameResult

GAME_FIELDS = [
    "event_id", "date", "home", "away", "home_score", "away_score", "neutral",
    "close_home_ml", "close_away_ml", "close_draw_ml",
    "close_home_spread", "close_home_spread_price", "close_away_spread_price",
    "close_total", "close_over_price", "close_under_price",
    "open_home_ml", "open_away_ml", "open_home_spread", "open_home_spread_price", "open_away_spread_price",
]
SNAPSHOT_FIELDS = ["seen_at", "event_id", "start", "home", "away", "market", "side", "point", "book", "price"]
PROP_FIELDS = ["seen_at", "event_id", "start", "home", "away", "player", "market", "line", "over_price", "under_price", "book"]


def _month(when: datetime) -> str:
    return when.strftime("%Y-%m")


def _lines(quotes: list[Quote]) -> dict[str, float | None]:
    row: dict[str, float | None] = {}
    for q in quotes:
        if q.market == "h2h":
            row[f"{q.side}_ml"] = q.american
        elif q.market == "spreads" and q.side in ("home", "away"):
            if q.side == "home":
                row["home_spread"] = q.point
            row[f"{q.side}_spread_price"] = q.american
        elif q.market == "totals" and q.side in ("over", "under"):
            row["total"] = q.point
            row[f"{q.side}_price"] = q.american
    return row


def game_rows(games: list[GameResult], close: dict[str, list[Quote]], opened: dict[str, list[Quote]]) -> list[dict]:
    rows = []
    for g in sorted(games, key=lambda g: g.date):
        if not g.completed:
            continue
        row = {
            "event_id": g.event_id, "date": g.date.isoformat(), "home": g.home, "away": g.away,
            "home_score": g.home_score, "away_score": g.away_score, "neutral": int(g.neutral),
        }
        row.update({f"close_{k}": v for k, v in _lines(close.get(g.event_id, [])).items()})
        row.update({f"open_{k}": v for k, v in _lines(opened.get(g.event_id, [])).items()})
        rows.append({k: row.get(k, "") for k in GAME_FIELDS})
    return rows


def write_games(folder: Path, sport: str, games: list[GameResult],
                close: dict[str, list[Quote]], opened: dict[str, list[Quote]]) -> int:
    """Rewrite the monthly game files for the months these games fall in. Returns files written."""
    by_month: dict[str, list[GameResult]] = defaultdict(list)
    for g in games:
        if g.completed:
            by_month[_month(g.date)].append(g)
    out = Path(folder) / sport
    out.mkdir(parents=True, exist_ok=True)
    for month, group in by_month.items():
        with open(out / f"{month}.csv", "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=GAME_FIELDS)
            w.writeheader()
            w.writerows(game_rows(group, close, opened))
    return len(by_month)


def _append(path: Path, fields: list[str], rows: list[dict]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    with open(path, "a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        if new:
            w.writeheader()
        w.writerows(rows)


def log_snapshots(folder: Path, scans, now: datetime) -> int:
    """Append every game line from this scan. Returns rows written."""
    total = 0
    for scan in scans:
        rows = []
        for ctx in scan.contexts.values():
            ev = ctx.event
            for q in ev.quotes:
                if q.market not in ("h2h", "spreads", "totals"):
                    continue
                rows.append({
                    "seen_at": now.isoformat(), "event_id": ev.id, "start": ev.commence_time.isoformat(),
                    "home": ev.home, "away": ev.away, "market": q.market, "side": q.side,
                    "point": "" if q.point is None else q.point, "book": q.book, "price": q.american,
                })
        _append(Path(folder) / scan.sport.key / f"{_month(now)}.csv", SNAPSHOT_FIELDS, rows)
        total += len(rows)
    return total


def log_prop_lines(folder: Path, scans, now: datetime) -> int:
    """Append every player prop line the scan fetched. Returns rows written."""
    total = 0
    for scan in scans:
        rows = []
        for ev, quotes in getattr(scan, "prop_lines", []):
            lines: dict[tuple, dict] = {}  # one row per player, stat, line and book
            for q in quotes:
                key = (q.player or "", q.market, q.point, q.book)
                row = lines.setdefault(key, {
                    "seen_at": now.isoformat(), "event_id": ev.id, "start": ev.commence_time.isoformat(),
                    "home": ev.home, "away": ev.away, "player": q.player or "", "market": q.market,
                    "line": "" if q.point is None else q.point, "over_price": "", "under_price": "", "book": q.book,
                })
                if q.side in ("over", "under"):
                    row[f"{q.side}_price"] = q.american
            rows.extend(lines.values())
        _append(Path(folder) / scan.sport.key / f"{_month(now)}.csv", PROP_FIELDS, rows)
        total += len(rows)
    return total
