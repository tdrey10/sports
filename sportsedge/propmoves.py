"""Where the money is going on player props (owner, Nov 5: "list the most bet on prop bets").

No free source publishes how much is bet on each prop, so this shows the next best thing: the
props whose line moved the most since we first saw it today. Books move a line when money piles
onto one side, so a line that went up means the over was getting hit, and down means the under.

Read from the prop lines we log every run (data/props/<sport>/<month>.csv), games not started yet.
"""

from __future__ import annotations

import csv
from datetime import datetime, timedelta
from pathlib import Path

SPORTS = ("nba", "nfl", "mlb")
LABEL = {"player_points": "points", "player_rebounds": "rebounds", "player_assists": "assists",
         "player_threes": "threes", "player_points_rebounds_assists": "pts+reb+ast",
         "player_pass_yds": "passing yards", "player_rush_yds": "rushing yards",
         "player_reception_yds": "receiving yards", "player_receptions": "catches",
         "batter_hits": "hits", "batter_total_bases": "total bases", "pitcher_strikeouts": "strikeouts"}
MIN_MOVE = 0.5
TOP = 15


def moves(root, now: datetime, hours: int = 36) -> list[dict]:
    """The props that moved the most since first seen, for games starting in the next `hours`."""
    out = []
    for sport in SPORTS:
        first: dict = {}
        last: dict = {}
        for f in sorted((Path(root) / "props" / sport).glob("*.csv"))[-2:]:
            with f.open() as fh:
                for r in csv.DictReader(fh):
                    try:
                        start = datetime.fromisoformat(r["start"])
                        line = float(r["line"])
                    except (KeyError, TypeError, ValueError):
                        continue
                    if not now < start <= now + timedelta(hours=hours):
                        continue
                    key = (r["event_id"], r["player"], r["market"])
                    row = {"sport": sport, "player": r["player"], "market": LABEL.get(r["market"], r["market"]),
                           "matchup": f"{r['away']} @ {r['home']}", "start": r["start"], "line": line,
                           "seen_at": r["seen_at"]}
                    if key not in first or r["seen_at"] < first[key]["seen_at"]:
                        first[key] = row
                    if key not in last or r["seen_at"] > last[key]["seen_at"]:
                        last[key] = row
        for key, a in first.items():
            b = last[key]
            move = round(b["line"] - a["line"], 1)
            if abs(move) >= MIN_MOVE:
                out.append({**{k: b[k] for k in ("sport", "player", "market", "matchup", "start")},
                            "open": a["line"], "now": b["line"], "move": move,
                            "side": "over" if move > 0 else "under"})
    # Biggest moves first, measured against the line's size (a 1-point move on 2.5 assists is bigger
    # than a 1-point move on 25.5 points).
    out.sort(key=lambda m: abs(m["move"]) / max(abs(m["open"]), 1.0), reverse=True)
    return out[:TOP]


__all__ = ["moves"]
