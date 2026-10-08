"""Sort the day's edges into cards: Lock / Dog / Prop of the Day, value plays,
early value plays and leans, each with a Kelly-based unit size."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta

from .edge import Pick, Settings, best_per_event, growth, qualifying
from .engine import SportScan
from .insights import reasons

TIER_LABELS = {
    "lock": "Lock of the Day",
    "dog": "Dog of the Day",
    "prop": "Prop of the Day",
    "value": "Value Play",
    "early": "Early Value",
    "lean": "Lean",
}
UNIT_TIERS = ("lock", "dog", "prop", "value", "early")

LOCK_MIN_ODDS = -250  # a lock still has to be worth the juice
DOG_MIN_ODDS = 120
DOG_MAX_ODDS = 350  # past this the variance swamps the edge for a headline pick
PROP_ODDS_RANGE = (-200, 200)
EARLY_HOURS = 24  # games further out than this are "early value"
MAX_UNITS = 3.0
BEST_BETS = 5  # daily top picks across spreads, totals, moneylines and props


@dataclass
class Card:
    tier: str
    units: float
    headline: str
    reasons: list[str]
    sport: str
    sport_name: str
    pick: Pick
    home: str = ""
    away: str = ""
    best_rank: int | None = None  # 1..BEST_BETS for the day's best bets

    def to_dict(self) -> dict:
        d = asdict(self)
        d["pick"] = self.pick.to_dict()
        return d


def units_for(pick: Pick) -> float:
    """Kelly stake as units (1 unit = 1% of bankroll), rounded to half units."""
    units = round(pick.kelly * 100 * 2) / 2
    return min(max(units, 0.5), MAX_UNITS)


def build_cards(
    scans: list[SportScan],
    settings: Settings,
    now: datetime,
    taken_today: set[str] | None = None,
    best_taken: int = 0,
    max_value: int = 12,
    max_early: int = 10,
    max_leans: int = 10,
    max_props: int = 8,
) -> list[Card]:
    """``taken_today`` holds day-level tiers (lock/dog/prop) already published
    today, so a Lock of the Day is never swapped out mid-day."""
    taken = set(taken_today or ())
    by_event: dict[str, SportScan] = {}
    game: list[Pick] = []
    props: list[Pick] = []
    for scan in scans:
        for ev_id in scan.contexts:
            by_event[ev_id] = scan
        game.extend(p for p in scan.picks if not p.is_prop)
        props.extend(p for p in scan.picks if p.is_prop)

    soon_cut = now + timedelta(hours=EARLY_HOURS)
    candidates = best_per_event(game, settings)
    soon = [p for p in candidates if p.commence_time <= soon_cut]
    later = [p for p in candidates if p.commence_time > soon_cut]
    chosen: list[tuple[str, Pick]] = []

    if "lock" not in taken:
        locks = [p for p in soon if p.american >= LOCK_MIN_ODDS]
        if locks:
            lock = max(locks, key=lambda p: (p.win_prob, p.ev))
            chosen.append(("lock", lock))
    if "dog" not in taken:
        dogs = [p for p in soon if DOG_MIN_ODDS <= p.american <= DOG_MAX_ODDS and p not in [c for _, c in chosen]]
        if dogs:
            chosen.append(("dog", max(dogs, key=lambda p: p.ev)))
    used = {id(p) for _, p in chosen}
    chosen += [("value", p) for p in soon if id(p) not in used][:max_value]
    chosen += [("early", p) for p in later][:max_early]

    prop_picks = qualifying(props, settings)
    lo, hi = PROP_ODDS_RANGE
    headline_props = [p for p in prop_picks if lo <= p.american <= hi]
    if headline_props and "prop" not in taken:
        chosen.append(("prop", headline_props[0]))
        prop_picks = [p for p in prop_picks if p is not headline_props[0]]
    chosen += [("value", p) for p in prop_picks[:max_props]]

    # Leans: games with no unit play where our side still has a small positive edge.
    covered = {p.event_id for _, p in chosen}
    leans = [
        p for p in game
        if p.event_id not in covered and 0 < p.ev < settings.min_ev and not p.warnings
    ]
    best_lean: dict[str, Pick] = {}
    for p in sorted(leans, key=lambda p: p.ev, reverse=True):
        best_lean.setdefault(p.event_id, p)
    chosen += [("lean", p) for p in list(best_lean.values())[:max_leans]]

    # Best bets: today's unit plays with the fastest bankroll growth.
    slots = max(BEST_BETS - best_taken, 0)
    ranked = sorted(
        (p for tier, p in chosen if tier in UNIT_TIERS and p.commence_time <= soon_cut),
        key=growth, reverse=True,
    )[:slots]
    best_rank = {id(p): best_taken + i + 1 for i, p in enumerate(ranked)}

    cards = []
    for tier, pick in chosen:
        scan = by_event.get(pick.event_id)
        headline, bullets = reasons(pick, scan) if scan else ("", [])
        ctx = scan.contexts.get(pick.event_id) if scan else None
        cards.append(
            Card(
                tier=tier,
                units=units_for(pick) if tier in UNIT_TIERS else 0.0,
                headline=headline,
                reasons=bullets,
                sport=pick.sport,
                sport_name=scan.sport.name if scan else pick.sport,
                pick=pick,
                home=ctx.home if ctx else "",
                away=ctx.away if ctx else "",
                best_rank=best_rank.get(id(pick)),
            )
        )
    return cards
