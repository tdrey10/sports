"""Parlays of the day: 2, 3, 4 and 8 legs, built from the day's best bets.

Each parlay is posted once a day and never changes after that. Legs come from
different games (no same-game parlays): today's unit plays, most likely to win
first. The 2-leg is the top two, the 3-leg the top three, and so on.

Unit plays (legitimate edges) come first. The 2- and 3-team parlays are posted
every day when possible: if there aren't enough unit plays by FILL_HOUR, the next
most likely winners that still show an edge (leans and sharp picks, 50%+ to win)
fill the open legs, and that parlay carries no units (tracked, for fun). The 4- and
8-team parlays never use filler.

A parlay multiplies the book's edge on every leg, so even strong legs make a
long shot: the page shows each parlay's real chance to hit (our win chances
multiplied together) and stakes stay small. Parlays keep their own record,
apart from the straight-bet money record.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta

from .odds import american_to_decimal

SIZES = (2, 3, 4, 8)
STAKE = {2: 0.5, 3: 0.25, 4: 0.25, 8: 0.1}  # units: small on purpose, every leg has to win
LEG_TIERS = ("lock", "dog", "value", "prop", "sharp")  # unit plays
FILL_TIERS = ("lean",)  # small edges with no units: filler for the daily 2- and 3-team parlays
FILL_SIZES = (2, 3)
FILL_HOUR = 10  # local time: give the day's real picks a chance to post before filling with leans
FILL_MIN_WIN = 0.5
LEG_HOURS = 24  # legs start within a day of posting (today's slate, not next week's)
# What one unit of a leg returns (stake included) once it settles.
_RETURN = {"lost": 0.0, "half_lost": 0.5, "push": 1.0, "void": 1.0}


@dataclass
class Parlay:
    id: str
    day: str
    created_at: str
    size: int
    legs: list[str]  # ledger entry ids
    leg_odds: list[float]  # each leg's price when the parlay was posted
    units: float
    decimal: float = 0.0
    hit_prob: float = 0.0  # our win chances multiplied together
    extra: dict = field(default_factory=dict)

    @property
    def american(self) -> float:
        d = self.decimal
        return round((d - 1) * 100) if d >= 2 else round(-100 / (d - 1))

    def to_dict(self) -> dict:
        d = asdict(self)
        d["american"] = self.american
        return d


def leg_pool(entries, now: datetime, today: str) -> list:
    """Today's open unit plays, one per game, most likely to win first."""
    cut = now + timedelta(hours=LEG_HOURS)
    open_today = [e for e in entries if e.day == today and e.status == "pending" and now < e.start <= cut]
    # Most likely to win first; edge score breaks ties.
    strong = sorted((e for e in open_today if e.tier in LEG_TIERS and e.units > 0),
                    key=lambda e: (-e.win_prob, -(e.score or 0)))
    out, games = [], set()
    for e in strong:
        if e.event_id not in games:
            games.add(e.event_id)
            out.append(e)
    return out


def fill_pool(entries, now: datetime, today: str, taken: set[str]) -> list:
    """Leans with an edge and 50%+ to win, one per game not already used, most likely first."""
    cut = now + timedelta(hours=LEG_HOURS)
    pool = sorted((e for e in entries if e.day == today and e.status == "pending" and now < e.start <= cut
                   and e.tier in FILL_TIERS and (e.ev or 0) > 0 and e.win_prob >= FILL_MIN_WIN),
                  key=lambda e: (-e.win_prob, -(e.score or 0)))
    out, games = [], set(taken)
    for e in pool:
        if e.event_id not in games:
            games.add(e.event_id)
            out.append(e)
    return out


def build(entries, existing: list[Parlay], now: datetime, today: str, local_hour: int = 0) -> list[Parlay]:
    """New parlays for sizes not yet posted today (once posted, a parlay never changes)."""
    entries = list(entries)
    posted = {p.size for p in existing if p.day == today}
    strong = leg_pool(entries, now, today)
    fill = fill_pool(entries, now, today, {e.event_id for e in strong}) if local_hour >= FILL_HOUR else []
    out = []
    for size in SIZES:
        if size in posted:
            continue
        if len(strong) >= size:
            legs = strong[:size]
        elif size in FILL_SIZES and len(strong) + len(fill) >= size:
            legs = strong + fill[:size - len(strong)]  # the daily 2- and 3-team: next best legs fill in
        else:
            continue
        prices = [e.current_odds if e.current_odds is not None else e.odds for e in legs]
        dec, hit = 1.0, 1.0
        for e, a in zip(legs, prices):
            dec *= american_to_decimal(a)
            hit *= e.win_prob
        pid = hashlib.sha1(f"parlay|{today}|{size}".encode()).hexdigest()[:16]
        units = STAKE[size] if all(e.units > 0 for e in legs) else 0.0
        out.append(Parlay(pid, today, now.isoformat(), size, [e.id for e in legs], prices, units,
                          decimal=round(dec, 4), hit_prob=round(hit, 4)))
    return out


def settle(p: Parlay, entries: dict) -> dict:
    """Status and profit from the legs: lost as soon as one leg loses, won when all are in.
    A no-unit parlay is scored as if it were 1 unit, for its record."""
    stake = p.units or 1.0
    legs = [entries.get(i) for i in p.legs]
    if any(e is None for e in legs):
        return {"status": "void", "profit": 0.0, "graded_at": None}
    if any(e.status == "lost" for e in legs):
        done = [e.graded_at for e in legs if e.status == "lost" and e.graded_at]
        return {"status": "lost", "profit": -stake, "graded_at": min(done) if done else None}
    if any(not e.settled for e in legs):
        live = any(e.status == "live" for e in legs)
        return {"status": "live" if live else "pending", "profit": None, "graded_at": None}
    ret = 1.0
    for e, a in zip(legs, p.leg_odds):
        d = american_to_decimal(a)
        ret *= (1 + d) / 2 if e.status == "half_won" else _RETURN.get(e.status, d)
    profit = round(stake * (ret - 1), 4)
    status = "won" if profit > 0 else "push" if profit == 0 else "lost"
    return {"status": status, "profit": profit,
            "graded_at": max((e.graded_at for e in legs if e.graded_at), default=None)}
