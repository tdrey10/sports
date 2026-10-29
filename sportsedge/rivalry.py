"""The Doctor vs Grim (owner, Oct 29: "You come out with your leans. And I'll give you my picks. We can see
whose record is better." Then: "Also track my parlay I give you. Can you call it The Doctor vs Grim" -
the owner is The Doctor, Script Leaks is Grim).

The owner's picks live in their own ledger (data/my_picks.json) and are graded exactly like ours, on
their own game. The scoreboard compares them with our leans (the no-money picks) posted since the
contest started, one unit flat on every pick on both sides. The owner's parlays are tracked the same way
(their legs in data/my_parlay_legs.json, graded on their own games) against our parlays.
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
LEGS = "my_parlay_legs.json"


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


def _card(root, sport, team, odds, now, market, point, side) -> Card:
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
    return Card(tier="lean", units=0.0, headline=f"Your pick: {selection} at {odds:+.0f}.", reasons=["Your pick."],
                sport=sport, sport_name=sport.upper(), pick=pick, home=home, away=away)


def add(root, sport: str, team: str, odds: float, now: datetime, market: str = "h2h",
        point: float | None = None, side: str | None = None) -> str:
    """Record one of the owner's picks: a team's moneyline (or spread with point, or a total with side
    over/under and point). Returns what was saved, in words."""
    card = _card(root, sport, team, odds, now, market, point, side)
    pick, selection, start = card.pick, card.pick.selection, card.pick.commence_time
    home, away = card.home, card.away
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


PARLAY_INBOX = "my_parlays_inbox.csv"
PARLAY_FIELDS = ["added_at", "parlay"] + INBOX_FIELDS[1:]


def parse_leg(spec: str) -> tuple:
    """'nba|Lakers|-120', 'nfl|Bills|-110|spread -3.5', 'nba|Lakers|-110|over 221.5' -> leg fields."""
    parts = [x.strip() for x in spec.split("|")]
    sport, team, odds = parts[0].lower(), parts[1], float(parts[2])
    market, point, side = "h2h", None, None
    if len(parts) > 3:
        kind, num = parts[3].split()
        if kind.lower() == "spread":
            market, point = "spreads", float(num)
        else:
            market, side, point = "totals", kind.lower(), float(num)
    return sport, team, odds, market, point, side


def send_parlay(root, legs: list[tuple], now: datetime) -> str:
    """Put a parlay in the inbox: every leg must be on an upcoming game we have."""
    import csv

    if len(legs) < 2:
        raise ValueError("a parlay needs 2+ legs")
    names = []
    for sport, team, odds, *_ in legs:
        found = _game(root, sport, team, now)
        if found is None:
            raise ValueError(f"no upcoming {sport.upper()} game found for {team}")
        names.append(f"{found[4]} {odds:+.0f}")
    pid = "doc-" + now.strftime("%Y%m%d%H%M%S")
    f = Path(root) / PARLAY_INBOX
    new = not f.exists()
    with f.open("a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=PARLAY_FIELDS)
        if new:
            w.writeheader()
        for sport, team, odds, market, point, side in legs:
            w.writerow({"added_at": now.isoformat(), "parlay": pid, "sport": sport, "team": team, "odds": odds,
                        "market": market, "point": "" if point is None else point, "side": side or ""})
    return f"In: {len(legs)}-leg parlay: " + " + ".join(names) + "."


def _file_parlays(root) -> int:
    import csv
    from collections import defaultdict

    from .ledger import entry_id
    from .odds import american_to_decimal
    from .parlays import Parlay

    f = Path(root) / PARLAY_INBOX
    if not f.exists():
        return 0
    groups = defaultdict(list)
    with f.open() as fh:
        for r in csv.DictReader(fh):
            groups[r["parlay"]].append(r)
    lp = Path(root) / LEGS
    ledger = Ledger.load(lp)
    have = {p.id for p in ledger.parlays}
    n = 0
    for pid, rows in groups.items():
        if pid in have:
            continue
        now = datetime.fromisoformat(rows[0]["added_at"])
        try:
            cards = [_card(root, r["sport"], r["team"], float(r["odds"]), now, r["market"] or "h2h",
                           float(r["point"]) if r["point"] else None, r["side"] or None) for r in rows]
        except ValueError:
            continue
        ledger.publish(cards, now)
        ids = [entry_id(c.pick) for c in cards]
        if not all(i in ledger.entries for i in ids):
            continue
        dec = 1.0
        for c in cards:
            dec *= american_to_decimal(c.pick.american)
        ledger.parlays.append(Parlay(id=pid, day=ledger.day_of(now), created_at=now.isoformat(), size=len(cards), legs=ids,
                                     leg_odds=[c.pick.american for c in cards], units=0.0, decimal=round(dec, 4),
                                     kind="doctor"))
        n += 1
    ledger.save(lp)
    return n


def refresh(provider, root, now: datetime) -> int:
    """File new picks and parlays from the inboxes, then grade them the same way ours are graded."""
    from .runner import refresh_scores

    _file_inbox(root)
    _file_parlays(root)
    graded = 0
    for p in (path(root), Path(root) / LEGS):
        if p.exists():
            ledger = Ledger.load(p)
            graded += refresh_scores(provider, ledger, now)
            ledger.save(p)
    return graded


def _parlay_record(parlays, entries) -> tuple[dict, list[dict]]:
    from .parlays import settle

    rows = []
    for p in parlays:
        s = settle(p, entries)
        if s["status"] != "void":
            rows.append((p, s))
    won = sum(s["status"] == "won" for _, s in rows)
    lost = sum(s["status"] == "lost" for _, s in rows)
    push = sum(s["status"] == "push" for _, s in rows)
    units = round(sum(s["profit"] or 0 for _, s in rows if s["profit"] is not None), 2)
    rec = {"won": won, "lost": lost, "push": push, "units": units,
           "pending": sum(s["status"] in ("pending", "live") for _, s in rows)}
    view = [{"odds": p.american, "status": s["status"],
             "legs": [f"{entries[i].selection} {a:+.0f}" + (f" ({entries[i].status})" if entries[i].settled else "")
                      for i, a in zip(p.legs, p.leg_odds) if i in entries]}
            for p, s in sorted(rows, key=lambda x: x[0].created_at, reverse=True)[:5]]
    return rec, view


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
    legs = Ledger.load(Path(root) / LEGS)
    you_p, your_parlays = _parlay_record(legs.parlays, legs.entries)
    grim_p, _ = _parlay_record([p for p in ours.parlays if p.created_at >= START.isoformat()], ours.entries)
    return {"since": START.isoformat(), "names": {"you": "The Doctor", "us": "Grim"},
            "leans": _record(leans), "you": _record(yours),
            "your_picks": [{"selection": e.selection, "matchup": e.matchup, "odds": e.odds, "start": e.start.isoformat(),
                            "status": e.status, "final": e.final} for e in recent],
            "parlays": {"you": you_p, "us": grim_p}, "your_parlays": your_parlays}


__all__ = ["add", "parse_leg", "refresh", "scoreboard", "send", "send_parlay"]
