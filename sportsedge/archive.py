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


GRADED_FIELDS = ["event_id", "start", "home", "away", "player", "market", "open_line", "close_line",
                 "close_over_price", "close_under_price", "actual", "result"]


def grade_prop_lines(props_folder: Path, out_folder: Path, sports, results_for, box_for, now: datetime) -> int:
    """Grade every logged prop line whose game is over: the player's actual stat against
    the first (open) and last (close) line seen before the start. Writes
    <out>/<sport>/<YYYY-MM>.csv once per game; returns the number of lines graded.

    This is the prop history the blueprint asks for: no prop idea can be tested without it.
    """
    from datetime import timedelta

    from .data.base import NameMatcher
    from .models.props import stat_value

    total = 0
    for cfg in sports:
        folder = Path(props_folder) / cfg.key
        if not folder.exists() or not cfg.props:
            continue
        done_ids: set[str] = set()
        out_dir = Path(out_folder) / cfg.key
        for f in out_dir.glob("*.csv") if out_dir.exists() else []:
            done_ids |= {r["event_id"] for r in csv.DictReader(open(f))}
        by_event: dict[str, list[dict]] = defaultdict(list)
        for f in sorted(folder.glob("*.csv")):
            for r in csv.DictReader(open(f)):
                if r["event_id"] not in done_ids and r["seen_at"] < r["start"]:
                    by_event[r["event_id"]].append(r)
        if not by_event:
            continue
        starts = [datetime.fromisoformat(rows[0]["start"]) for rows in by_event.values()]
        games = {g.event_id: g for g in results_for(cfg, min(starts) - timedelta(days=1), now) if g.completed}
        for event_id, rows in by_event.items():
            game = games.get(event_id)
            if game is None:
                continue  # not finished (or not found) yet
            box = box_for(cfg, game)
            if not box:
                continue
            players = NameMatcher({pg.player: pg for pg in box}, threshold=0.88)
            stats = {pg.player: pg.stats for pg in box}
            lines: dict[tuple[str, str], list[dict]] = defaultdict(list)
            for r in sorted(rows, key=lambda r: r["seen_at"]):
                lines[(r["player"], r["market"])].append(r)
            out = []
            for (player, market), seen in lines.items():
                spec = cfg.props.get(market)
                name = players.match(player)
                actual = stat_value(spec, stats[name]) if spec and name else None
                first, last = seen[0], seen[-1]
                line = float(last["line"]) if last["line"] else None
                if actual is None or line is None:
                    result = "void"
                else:
                    result = "over" if actual > line else "under" if actual < line else "push"
                out.append({
                    "event_id": event_id, "start": first["start"], "home": first["home"], "away": first["away"],
                    "player": player, "market": market, "open_line": first["line"], "close_line": last["line"],
                    "close_over_price": last["over_price"], "close_under_price": last["under_price"],
                    "actual": "" if actual is None else actual, "result": result,
                })
            _append(out_dir / f"{_month(game.date)}.csv", GRADED_FIELDS, out)
            total += len(out)
    return total


def prop_summary(graded_folder: Path) -> dict[str, dict]:
    """Per sport and stat: how often the over hit, and how often the line moved before the start."""
    out: dict[str, dict] = {}
    for sport_dir in sorted(Path(graded_folder).glob("*")) if Path(graded_folder).exists() else []:
        tally: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        for f in sport_dir.glob("*.csv"):
            for r in csv.DictReader(open(f)):
                t = tally[r["market"]]
                t[r["result"]] += 1
                if r["open_line"] and r["close_line"] and r["open_line"] != r["close_line"]:
                    t["moved"] += 1
        out[sport_dir.name] = {
            m: {"lines": sum(v for k, v in t.items() if k != "moved"), "over": t["over"], "under": t["under"],
                "push": t["push"], "void": t["void"], "moved": t["moved"]}
            for m, t in tally.items()
        }
    return out


def moneyline_history(folder: Path, sport: str) -> dict[str, dict[str, list[tuple[str, float]]]]:
    """event id -> side -> [(seen_at, best moneyline)] from the snapshot log, oldest first."""
    out: dict[str, dict[str, dict[str, float]]] = defaultdict(lambda: defaultdict(dict))
    for f in sorted((Path(folder) / sport).glob("*.csv")) if (Path(folder) / sport).exists() else []:
        for r in csv.DictReader(open(f)):
            if r["market"] != "h2h" or r["seen_at"] >= r["start"]:
                continue
            price = float(r["price"])
            seen = out[r["event_id"]][r["side"]]
            seen[r["seen_at"]] = max(seen.get(r["seen_at"], price), price)  # best price at that look
    return {ev: {side: sorted(t.items()) for side, t in sides.items()} for ev, sides in out.items()}
