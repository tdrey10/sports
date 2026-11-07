"""The pick card the owner asked for (Nov 7, after the hockey card: "that's how I want these picks coming out
on the board, all sports"): for each lean, the report notes that back the side we picked (the other team's
goalie is cold, they're missing a starter, their record is luckier than their goal differential) and the
ones that cut against it (a "watch out"), plus a stay-off list of favorites too pricey for what they pay.

The notes come from the sport reports we already build (nba/nfl/ncaaf/nhl _report.json). Each note names a
team; whether it helps or hurts that team is read from its wording.
"""

from __future__ import annotations

from datetime import datetime, timedelta

HURTS = ("bum", "lucky:", "without", "missing", " out ", " out (", "questionable", "doubtful", "the bill's coming")
HELPS = ("cookin", "due:", "unlucky", "been unlucky")
STAY_OFF_ML = -250  # a favorite this pricey risks a lot to win a little


def _subject(note: str, home: str, away: str) -> str | None:
    hits = [(note.find(t), t) for t in (home, away) if t and t in note]
    if not hits:
        # Reports shorten names ("Rangers without ..."): match on the last word of the team name.
        hits = [(note.find(t.split()[-1]), t) for t in (home, away) if t and t.split()[-1] in note]
    return min(hits)[1] if hits else None


def _effect(note: str) -> int:
    """+1 the note is good for the team it names, -1 bad, 0 neutral."""
    low = note.lower()
    if "one-goal games" in low or "one-score games" in low or "close games" in low:
        return -1 if " won " in f" {low}" and low.find("won") < low.find("more") else 1
    if any(w in low for w in HELPS):
        return 1
    if any(w in low for w in HURTS):
        return -1
    return 0


def side_notes(home: str, away: str, side: str, notes: list[str]) -> tuple[list[str], list[str]]:
    """(notes that back the picked side, notes that cut against it)."""
    team, opp = (home, away) if side == "home" else (away, home)
    good, bad = [], []
    for n in notes or []:
        who, eff = _subject(n, home, away), _effect(n)
        if who is None or eff == 0:
            continue
        backs = (who == team and eff > 0) or (who == opp and eff < 0)
        (good if backs else bad).append(n.strip())
    return good[:3], bad[:2]


def _matchups(reports: dict) -> dict[tuple, dict]:
    out = {}
    for sport, rep in (reports or {}).items():
        for m in (rep or {}).get("matchups") or []:
            out[(sport, m.get("home"), m.get("away"))] = m
    return out


def annotate(entries: list[dict], reports: dict) -> None:
    """Add notes_for / notes_against to each open side pick that has a report matchup."""
    games = _matchups(reports)
    for d in entries:
        if d.get("settled") or d.get("player") or d.get("market") not in ("h2h", "spreads") \
                or d.get("side") not in ("home", "away"):
            continue
        m = games.get((d.get("sport"), d.get("home"), d.get("away")))
        if m:
            d["notes_for"], d["notes_against"] = side_notes(d["home"], d["away"], d["side"], m.get("notes") or [])


def stay_off(reports: dict, now: datetime, hours: int = 30) -> list[dict]:
    """Favorites at STAY_OFF_ML or shorter in the next `hours`: too pricey for what they pay."""
    out = []
    for sport, rep in (reports or {}).items():
        if sport == "tennis":
            continue
        for m in (rep or {}).get("matchups") or []:
            try:
                start = datetime.fromisoformat(str(m.get("start")).replace("Z", "+00:00"))
            except ValueError:
                continue
            if not now < start <= now + timedelta(hours=hours):
                continue
            for side in ("home", "away"):
                ml = m.get(f"{side}_ml")
                if isinstance(ml, (int, float)) and ml <= STAY_OFF_ML:
                    out.append({"sport": sport, "team": m[side], "odds": ml, "start": m["start"],
                                "matchup": f"{m['away']} @ {m['home']}"})
    return sorted(out, key=lambda x: x["odds"])


__all__ = ["annotate", "side_notes", "stay_off"]
