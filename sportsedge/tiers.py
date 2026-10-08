"""Sort the day's edges into cards: Lock / Dog / Prop of the Day, value plays,
early value plays and leans, each with a Kelly-based unit size."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta

from .edge import Pick, Settings, best_per_event, edge_score, qualifying
from .engine import SportScan
from .insights import reasons
from .spots import SPOTS, is_dog_price

TIER_LABELS = {
    "lock": "Lock of the Day",
    "dog": "Dog of the Day",
    "prop": "Prop of the Day",
    "value": "Value Play",
    "early": "Early Value",
    "lean": "Lean",
    "live": "Live Bet",
    "live_win": "Live: Most Likely to Win",
    "live_plus": "Live Plus Money",
}
LIVE_TIERS = ("live", "live_win", "live_plus")
UNIT_TIERS = ("lock", "dog", "prop", "value", "early") + LIVE_TIERS

LOCK_MIN_ODDS = -150  # never bet worse than -150, the Lock included
# The Lock of the Day is the safest pick, not the biggest edge, so it must clear every check:
LOCK_MIN_PROB = 0.58  # model alone gives it at least this
LOCK_MAX_GAP = 0.06  # model and the no-vig market agree to within 6 points
LOCK_MIN_GAMES = 6  # both teams have enough games this season to trust their ratings
DOG_MIN_ODDS = 120
DOG_MAX_ODDS = 350  # past this the variance swamps the edge for a headline pick
PROP_ODDS_RANGE = (-200, 200)
EARLY_HOURS = 24  # games further out than this are "early value"
MAX_UNITS = 3.0
BEST_BETS = 10  # most daily top picks across spreads, totals, moneylines and props
BEST_MIN_SCORE = 40  # a best bet needs this edge score (and a positive calibrated edge); fewer is fine


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
    score: float | None = None  # edge score 0-100 (see edge.edge_score)
    spots: list[str] = field(default_factory=list)  # early-bet spots this pick is in (tracked separately)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["pick"] = self.pick.to_dict()
        return d


LONGSHOT_ODDS = 400
LONGSHOT_MAX_UNITS = 0.5


UNPROVEN_MAX_UNITS = 0.5
STUDY_MARKET = {"h2h": "moneyline", "spreads": "spreads", "totals": "over/under"}


def market_key(pick: Pick) -> str:
    return f"{pick.sport}:{'props' if pick.is_prop else STUDY_MARKET.get(pick.market, pick.market)}"


def is_proven(pick: Pick, settings: Settings | None) -> bool:
    """True when this sport and bet type beat the close in the blind study (or no studies are loaded)."""
    return settings is None or settings.proven is None or market_key(pick) in settings.proven


def units_for(pick: Pick, settings: Settings | None = None) -> float:
    """Quarter-Kelly stake as units (1 unit = 1% of bankroll), rounded to half units.

    Long shots (+400 and up) are capped at half a unit: their win probability
    is the hardest number to get right, and Kelly sizes them off it. So is any
    sport and bet type that hasn't been proven in a blind test.
    """
    units = round(pick.kelly * 100 * 2) / 2
    cap = LONGSHOT_MAX_UNITS if pick.american >= LONGSHOT_ODDS else MAX_UNITS
    if not is_proven(pick, settings):
        cap = min(cap, UNPROVEN_MAX_UNITS)
    return min(max(units, 0.5), cap)


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

    # No bet on a game where either team's injury report is unknown; those picks can
    # still show as leans (no units), so they are tracked.
    unknown = {
        ev_id for scan in scans for ev_id, ctx in scan.contexts.items() if ctx.injuries_unknown
    }
    blocked = [p for p in game + props if p.event_id in unknown]
    game = [p for p in game if p.event_id not in unknown]
    props = [p for p in props if p.event_id not in unknown]

    soon_cut = now + timedelta(hours=EARLY_HOURS)
    candidates = best_per_event(game, settings)
    soon = [p for p in candidates if p.commence_time <= soon_cut]
    later = [p for p in candidates if p.commence_time > soon_cut]
    chosen: list[tuple[str, Pick]] = []

    def solid(p: Pick) -> bool:
        scan = by_event.get(p.event_id)
        ctx = scan.contexts.get(p.event_id) if scan else None
        if p.warnings or p.market_prob is None or p.model_prob < LOCK_MIN_PROB:
            return False
        if abs(p.model_prob - p.market_prob) > LOCK_MAX_GAP or p.market_prob < 0.5:
            return False
        if scan is None or ctx is None or scan.ratings is None:
            return False
        played = scan.ratings.games_played
        return played.get(ctx.home, 0) >= LOCK_MIN_GAMES and played.get(ctx.away, 0) >= LOCK_MIN_GAMES

    if "lock" not in taken:
        locks = [p for p in soon if p.american >= LOCK_MIN_ODDS and solid(p)]
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
        and p.american >= settings.max_favorite
    ] + [
        p for p in blocked
        if not p.is_prop and p.ev > 0 and not p.warnings and p.american >= settings.max_favorite
    ]
    best_lean: dict[str, Pick] = {}
    for p in sorted(leans, key=lambda p: p.ev, reverse=True):
        best_lean.setdefault(p.event_id, p)
    chosen += [("lean", p) for p in list(best_lean.values())[:max_leans]]

    # Best bets: today's unit plays ranked by edge score, only those that clear the bar.
    scores = {id(p): edge_score(p, settings) for _, p in chosen}
    slots = max(BEST_BETS - best_taken, 0)
    contenders = [
        p for tier, p in chosen
        if tier in UNIT_TIERS and p.commence_time <= soon_cut and not p.warnings
        and scores[id(p)][0] >= BEST_MIN_SCORE and scores[id(p)][2] > 0
    ]
    ranked = sorted(contenders, key=lambda p: scores[id(p)][0], reverse=True)[:slots]
    best_rank = {id(p): best_taken + i + 1 for i, p in enumerate(ranked)}

    cards = []
    for tier, pick in chosen:
        scan = by_event.get(pick.event_id)
        headline, bullets = reasons(pick, scan) if scan else ("", [])
        if best_rank.get(id(pick)):
            sc, cq, cev = scores[id(pick)]
            bullets.insert(0, f"Edge score {sc:.0f}/100: {cev:+.1%} edge after the studies' calibration, "
                              f"cashes {cq:.0%} of the time.")
        ctx = scan.contexts.get(pick.event_id) if scan else None
        in_spots = []
        if ctx and pick.market == "h2h" and is_dog_price(pick.american):
            in_spots = list(ctx.spots.get(pick.side, []))
        elif ctx and pick.market == "totals" and pick.side == "under":
            in_spots = list(ctx.spots.get("under", []))
        if ctx and ctx.wind_mph is not None and pick.market == "totals":
            bullets.append(f"Wind forecast at kickoff: {ctx.wind_mph:.0f} mph (outdoor stadium).")
        if in_spots:
            for key in in_spots:
                proven = f"{pick.sport}:{key}" in settings.spot_weights
                bullets.append(f"Spot: {SPOTS[key].label}"
                               + (" (proven blind: weighted into the read)." if proven
                                  else " (tracked; not proven blind yet, so it adds nothing to the read)."))
        if tier in UNIT_TIERS and not is_proven(pick, settings):
            bullets.append("Sized at ½ unit: this sport and bet type hasn't beaten the closing line in a blind test yet.")
        if ctx and ctx.injuries_unknown:
            bullets.insert(0, f"No bet: no injury report for {', '.join(ctx.injuries_unknown)}, "
                              "so injuries are unknown. Tracked as a lean only.")
        if tier == "lock":
            bullets.insert(0, f"Passed every Lock check: our model ({pick.model_prob:.0%}) and the market "
                              f"({pick.market_prob:.0%}) agree, no data warnings, and both teams have "
                              f"{LOCK_MIN_GAMES}+ games this season.")
        ctx = scan.contexts.get(pick.event_id) if scan else None
        cards.append(
            Card(
                tier=tier,
                units=units_for(pick, settings) if tier in UNIT_TIERS else 0.0,
                headline=headline,
                reasons=bullets,
                sport=pick.sport,
                sport_name=scan.sport.name if scan else pick.sport,
                pick=pick,
                home=ctx.home if ctx else "",
                away=ctx.away if ctx else "",
                best_rank=best_rank.get(id(pick)),
                score=scores[id(pick)][0],
                spots=in_spots,
            )
        )
    return cards
