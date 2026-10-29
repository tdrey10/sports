"""You vs. the leans (owner, Oct 29: "You come out with your leans. And I'll give you my picks. We can see
whose record is better.").

The owner's picks live in their own ledger (data/my_picks.json) and are graded exactly like ours, on
their own game. The scoreboard compares them with our leans (the no-money picks) posted since the
contest started, one unit flat on every pick on both sides.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from .data.base import NameMatcher
from .edge import Pick
from .ledger import Ledger
from .tiers import Card

START = datetime(2026, 10, 29, 7, tzinfo=timezone.utc)  # midnight Pacific, Oct 29
LEAN_TIERS = ("lean", "early", "value", "lock", "dog")
FILE = "my_picks.json"


def path(root) -> Path:
    return Path(root) / FILE


def _game(root, sport: str, team: str, now: datetime):
    """(event_id, start, home, away) of the next game for this team, from our saved line snapshots."""
    import csv

    rows = []
    for f in sorted((Path(root) / "odds" / sport).glob("*.csv"))[-2:]:
        with f.open() as fh:
            rows.extend(csv.DictReader(fh))
    games = {}
    for r in rows:
        start = datetime.fromisoformat(r["start"])
        if start > now - timedelta(hours=1):
            games[r["event_id"]] = (start, r["home"], r["away"])
    m = NameMatcher({t for _, h, a in games.values() for t in (h, a)})
    name = m.match(team)
    hits = sorted((v[0], eid, v[1], v[2]) for eid, v in games.items() if name in (v[1], v[2]))
    if not hits:
        return None
    start, eid, home, away = hits[0]
    return eid, start, home, away, name


def add(root, sport: str, team: str, odds: float, now: datetime, market: str = "h2h",
        point: float | None = None, side: str | None = None) -> str:
    """Record one of the owner's picks: a team's moneyline (or spread with point, or a total with side
    over/under and point). Returns what was saved, in words."""
    found = _game(root, sport, team, now)
    if found is None:
        raise ValueError(f"no upcoming {sport.upper()} game found for {team}")
    eid, start, home, away, name = found
    if market == "totals":
        sel_side, selection = side, f"{side.title()} {point:g}"
    else:
        sel_side = "home" if name == home else "away"
        selection = f"{name} ML" if market == "h2h" else f"{name} {point:+g}"
    pick = Pick(sport=sport, event_id=eid, commence_time=start, matchup=f"{away} @ {home}", market=market,
                market_label={"h2h": "Moneyline", "spreads": "Spread", "totals": "Total"}[market], selection=selection,
                side=sel_side, point=point, player=None, book="", american=float(odds), model_prob=0.5,
                market_prob=None, win_prob=0.5, push_prob=0.0, fair_american=0.0, ev=0.0, kelly=0.0, stake=None,
                projection="", books=0)
    card = Card(tier="lean", units=0.0, headline=f"Your pick: {selection} at {odds:+.0f}.", reasons=["Your pick."],
                sport=sport, sport_name=sport.upper(), pick=pick, home=home, away=away)
    ledger = Ledger.load(path(root))
    added = ledger.publish([card], now)
    ledger.save(path(root))
    if not added:
        return f"Already have it: {selection} ({away} @ {home})."
    return f"Saved: {selection} at {odds:+.0f}, {away} @ {home}, {start.astimezone().strftime('%a %b %d %H:%M UTC')}."


INBOX = "my_picks_inbox.csv"  # picks as sent in chat; the engine files them (the inbox is never rewritten by a run)
INBOX_FIELDS = ["added_at", "sport", "team", "odds", "market", "point", "side"]


def send(root, sport: str, team: str, odds: float, now: datetime, market: str = "h2h",
         point: float | None = None, side: str | None = None) -> str:
    """Put a pick in the inbox (checked against our saved games now, filed on the next run)."""
    import csv

    found = _game(root, sport, team, now)
    if found is None:
        raise ValueError(f"no upcoming {sport.upper()} game found for {team}")
    f = Path(root) / INBOX
    new = not f.exists()
    with f.open("a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=INBOX_FIELDS)
        if new:
            w.writeheader()
        w.writerow({"added_at": now.isoformat(), "sport": sport, "team": team, "odds": odds, "market": market,
                    "point": "" if point is None else point, "side": side or ""})
    _, start, home, away, name = found
    return f"In: {name} at {odds:+.0f} ({away} @ {home})."


def _file_inbox(root) -> int:
    import csv

    f = Path(root) / INBOX
    if not f.exists():
        return 0
    n = 0
    with f.open() as fh:
        for r in csv.DictReader(fh):
            try:
                msg = add(root, r["sport"], r["team"], float(r["odds"]), datetime.fromisoformat(r["added_at"]),
                          r["market"] or "h2h", float(r["point"]) if r["point"] else None, r["side"] or None)
            except ValueError:
                continue
            n += msg.startswith("Saved")
    return n


def refresh(provider, root, now: datetime) -> int:
    """File new picks from the inbox, then grade the owner's picks the same way ours are graded."""
    _file_inbox(root)
    p = path(root)
    if not p.exists():
        return 0
    from .runner import refresh_scores

    ledger = Ledger.load(p)
    graded = refresh_scores(provider, ledger, now)
    ledger.save(p)
    return graded


def _record(entries) -> dict:
    won = sum(e.status in ("won", "half_won") for e in entries)
    lost = sum(e.status in ("lost", "half_lost") for e in entries)
    push = sum(e.status == "push" for e in entries)
    units = round(sum(e.profit or 0 for e in entries if e.settled), 2)
    return {"won": won, "lost": lost, "push": push, "units": units,
            "pending": sum(not e.settled for e in entries)}


def scoreboard(ours: Ledger, root) -> dict | None:
    """Both records since the contest started, one unit flat each."""
    p = path(root)
    mine = Ledger.load(p) if p.exists() else None
    leans = [e for e in ours.entries.values() if e.units == 0 and e.tier in LEAN_TIERS and e.start >= START
             and e.status != "void" and not e.player]
    yours = [e for e in (mine.entries.values() if mine else []) if e.status != "void"]
    recent = sorted(yours, key=lambda e: e.start, reverse=True)[:10]
    return {"since": START.isoformat(), "leans": _record(leans), "you": _record(yours),
            "your_picks": [{"selection": e.selection, "matchup": e.matchup, "odds": e.odds, "start": e.start.isoformat(),
                            "status": e.status, "final": e.final} for e in recent]}


__all__ = ["add", "refresh", "scoreboard", "send"]
