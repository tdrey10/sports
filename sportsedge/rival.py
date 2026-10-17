"""Head to head: our picks against the D503 engine (the owner's brother's), graded the same way.

His picks are public (D503therapper/autonomous-crypto-engine, data/sports/picks.json). Both
records start the same day (his first pick) and count only straight bets: money plays at the
units each engine put on them, and no-unit leans as if 1 unit, so neither side looks better by
how much it posts. Parlays are left out of both.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import requests

log = logging.getLogger(__name__)

URL = "https://raw.githubusercontent.com/D503therapper/autonomous-crypto-engine/main/data/sports/picks.json"
STRAIGHT = ("lock", "dog", "play", "solo", "lean")


def fetch(dest: Path, session: requests.Session | None = None) -> list[dict]:
    """His latest picks (saved to ``dest``); the saved copy when GitHub can't be reached."""
    try:
        r = (session or requests).get(URL, timeout=20)
        r.raise_for_status()
        picks = r.json()
        Path(dest).write_text(json.dumps(picks))
        return picks
    except (requests.RequestException, ValueError, OSError) as exc:
        log.info("rival picks unavailable: %s", exc)
    try:
        return json.loads(Path(dest).read_text())
    except (OSError, ValueError):
        return []


def _blank() -> dict:
    return {"won": 0, "lost": 0, "push": 0, "units": 0.0, "staked": 0.0}


def _add(rec: dict, status: str, stake: float, profit: float) -> None:
    rec[{"won": "won", "lost": "lost"}.get(status, "push")] += 1
    rec["units"] = round(rec["units"] + profit, 2)
    rec["staked"] = round(rec["staked"] + stake, 2)


def _theirs(picks: list[dict], since: str) -> dict:
    out = {"money": _blank(), "leans": _blank()}
    for p in picks:
        if p.get("kind") not in STRAIGHT or len(p.get("legs") or [None]) > 1 or (p.get("date") or "") < since:
            continue
        status = p.get("status")
        if status not in ("won", "lost", "push"):
            continue
        u = float(p.get("units") or 0)
        stake = u if u > 0 else 1.0
        dec = float(p.get("dec") or 0)
        profit = stake * (dec - 1) if status == "won" else -stake if status == "lost" else 0.0
        _add(out["money" if u > 0 else "leans"], status, stake, profit)
    return out


def _ours(entries: list[dict], since: str) -> dict:
    out = {"money": _blank(), "leans": _blank()}
    for e in entries:
        if (e.get("day") or "") < since or e.get("status") not in ("won", "lost", "push") or e.get("profit") is None:
            continue
        u = float(e.get("units") or 0)
        _add(out["money" if u > 0 else "leans"], e["status"], u if u > 0 else 1.0, float(e["profit"]))
    return out


def head_to_head(picks: list[dict], entries: list[dict], today: str) -> dict | None:
    if not picks:
        return None
    since = min((p.get("date") for p in picks if p.get("date")), default=None)
    if not since:
        return None
    todays = [{"kind": p.get("kind"), "units": p.get("units") or 0, "american": p.get("american"),
               "status": p.get("status"),
               "legs": [{"league": l.get("league"), "team": l.get("team"), "market": l.get("market"),
                         "line": l.get("line"), "edge": l.get("edge")} for l in (p.get("legs") or [])[:1]]}
              for p in picks if p.get("date") == today and p.get("kind") in STRAIGHT]
    return {"since": since, "theirs": _theirs(picks, since), "ours": _ours(entries, since), "today": todays}


__all__ = ["fetch", "head_to_head", "URL"]
