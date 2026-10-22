"""Head to head: our picks against the D503 engine (the owner's brother's), graded the same way.

His picks are public (D503therapper/autonomous-crypto-engine, data/sports/picks.json). His record
is shown as his own dashboard reports it (docs/sports/index.html: the unit plays record, which
also counts his early value plays and parlay legs that carried units as straight bets); our
count from his picks file is only the fallback when that page can't be read. Leans are a W-L
only on both sides: no units go on them.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

import requests

log = logging.getLogger(__name__)

URL = "https://raw.githubusercontent.com/D503therapper/autonomous-crypto-engine/main/data/sports/picks.json"
PAGE = "https://raw.githubusercontent.com/D503therapper/autonomous-crypto-engine/main/docs/sports/index.html"
_UNITS = re.compile(r'UNIT PLAYS RECORD</div><div class="ovr-r">(\d+)-(\d+)(?:-(\d+))?</div>'
                    r'<div class="ovr-p[^"]*">([+-])([\d.]+) UNITS · ROI ([+-]?\d+)%')
_LEANS = re.compile(r'LEANS RECORD</div><div class="ovr-r">(\d+)-(\d+)(?:-(\d+))?</div>')


def reported(html: str) -> dict | None:
    """His unit plays and leans records exactly as his dashboard shows them, or None."""
    m = _UNITS.search(html or "")
    if not m:
        return None
    w, l, p, sign, units, roi = m.groups()
    u = float(units) * (1 if sign == "+" else -1)
    r = int(roi) / 100
    out = {"money": {"won": int(w), "lost": int(l), "push": int(p or 0), "units": u,
                     "staked": round(u / r, 2) if r else 0.0}, "reported": True}
    m2 = _LEANS.search(html)
    if m2:
        out["leans"] = {"won": int(m2.group(1)), "lost": int(m2.group(2)), "push": int(m2.group(3) or 0),
                        "units": 0.0, "staked": 0.0}
    return out


def fetch_reported(dest: Path, session: requests.Session | None = None) -> dict | None:
    """His dashboard's own record (saved to ``dest``); the saved page when GitHub can't be reached."""
    try:
        r = (session or requests).get(PAGE, timeout=20)
        r.raise_for_status()
        Path(dest).write_text(r.text)
        return reported(r.text)
    except (requests.RequestException, OSError) as exc:
        log.info("rival dashboard unavailable: %s", exc)
    try:
        return reported(Path(dest).read_text())
    except OSError:
        return None
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


def head_to_head(picks: list[dict], entries: list[dict], today: str, theirs: dict | None = None) -> dict | None:
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
    mine = _theirs(picks, since)
    if theirs:  # his dashboard's own count wins over ours
        mine = {"money": theirs["money"], "leans": theirs.get("leans", mine["leans"]), "reported": True}
    return {"since": since, "theirs": mine, "ours": _ours(entries, since), "today": todays}


__all__ = ["fetch", "fetch_reported", "reported", "head_to_head", "URL", "PAGE"]
