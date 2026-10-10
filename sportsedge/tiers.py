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
    "sharp": "Sharp Value",
}
LIVE_TIERS = ("live", "live_win", "live_plus")
UNIT_TIERS = ("lock", "dog", "prop", "value", "early") + LIVE_TIERS

LOCK_MIN_ODDS = -170  # never bet worse than -170, the Lock included
# The Lock of the Day is the safest pick, not the biggest edge, so it must clear every check:
LOCK_MIN_PROB = 0.62  # our model alone gives it at least this
LOCK_MIN_MARKET = 0.55  # and the market (no vig) also has it as a clear favorite
LOCK_MIN_CASH = 0.60  # calibrated chance to cash, after the studies' correction
LOCK_MIN_SCORE = 50  # edge score: a real edge on a likely winner
LOCK_MAX_GAP = 0.06  # model and the no-vig market agree to within 6 points
LOCK_MIN_GAMES = 8  # both teams have enough games this season to trust their ratings
DOG_MIN_ODDS = 100  # the blueprint: dogs +100 to +220 only
DOG_MAX_ODDS = 220
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


def backed(pick: Pick, settings: Settings | None) -> bool:
    """Posted at all only when its sport and bet type beat the closing line in the blind study.
    (No studies loaded at all, as in the demo and tests, leaves everything open.)"""
    if settings is not None and settings.clv_trust.get(market_key(pick)) == 0.0:
        return False  # our own picks of this kind keep losing to the closing line: paused
    if settings is None or not settings.require_proven:
        return True  # real-edge picks post; unproven ones are sized at half a unit (units_for)
    return settings.proven is None or market_key(pick) in settings.proven


def is_proven(pick: Pick, settings: Settings | None) -> bool:
    """True when this sport and bet type beat the close in the blind study (or no studies are loaded)."""
    return settings is None or settings.proven is None or market_key(pick) in settings.proven


TRACK_MIN_BETS = 20
LEGIT_MIN_WIN = 0.45  # value and early plays: a realistic winner, not a long shot dressed up as value


def price_band(american: float) -> str:
    """The studies' price bands (see study.band)."""
    if american <= -150:
        return "favorites (-150 or shorter)"
    if american < 150:
        return "coin flips (-149 to +149)"
    return "underdogs (+150 or longer)"


def track_records(pick: Pick, settings: Settings | None) -> list[tuple[str, dict]]:
    """Blind study records for bets like this one: same sport and bet type, same price band."""
    if settings is None or pick.is_prop:
        return []
    keys = [("all", f"{pick.sport}:all"),
            (STUDY_MARKET.get(pick.market, pick.market), market_key(pick)),
            (price_band(pick.american).split(" (")[0], f"{pick.sport}:{price_band(pick.american)}")]
    return [(label, settings.track[k]) for label, k in keys
            if k in settings.track and (settings.track[k].get("bets") or 0) >= TRACK_MIN_BETS]


def losing_kind(pick: Pick, settings: Settings | None) -> str | None:
    """The kind of bet (bet type or price band) that lost in the blind study by more than luck, if any."""
    for label, r in track_records(pick, settings):
        if r.get("roi") is not None and r["roi"] + (r.get("se") or 0.0) < 0:
            return label
    return None


def not_legit(pick: Pick, settings: Settings | None, min_win: float = 0.0) -> str | None:
    """Why a pick can't be posted with units, or None. Every unit play needs a market price
    to check our number against, a kind of bet that isn't losing in the blind study, a
    positive edge after the studies' calibration and (value/early) a realistic chance to win."""
    if not pick.is_prop and pick.market_prob is None:
        return "no market price to check our number against"
    if kind := losing_kind(pick, settings):
        return f"{pick.sport.upper()} {kind if kind != 'all' else ''} bets lost in the blind study by more than luck".replace("  ", " ")
    if settings is not None and edge_score(pick, settings)[2] <= 0:
        return "no edge left after the studies' calibration"
    if pick.win_prob < min_win:
        return f"wins only {pick.win_prob:.0%} of the time on our numbers"
    return None


def track_line(pick: Pick, settings: Settings | None) -> str | None:
    recs = track_records(pick, settings)
    if not recs:
        return None
    parts = [f"{label} {r['won']:.0f}-{r['lost']:.0f} ({r['roi']:+.1%})" for label, r in recs if r.get("roi") is not None]
    return "Blind study, bets like this in " + pick.sport.upper() + ": " + ", ".join(parts) + "." if parts else None


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
    if settings is not None:
        units *= settings.clv_trust.get(market_key(pick), 1.0)  # adaptive: our CLV record
        units = round(units * 2) / 2
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
    every: list[Pick] = []  # every game-line side, backed or not: only for the daily Lock fallback
    for scan in scans:
        for ev_id in scan.contexts:
            by_event[ev_id] = scan
        every.extend(p for p in scan.picks if not p.is_prop)
        # Only bets backed by a blind test: no maybes on the board.
        game.extend(p for p in scan.picks if not p.is_prop and backed(p, settings))
        props.extend(p for p in scan.picks if p.is_prop and backed(p, settings))

    # No bet on a game where either team's injury report is unknown; those picks can
    # still show as leans (no units), so they are tracked.
    unknown = {
        ev_id for scan in scans for ev_id, ctx in scan.contexts.items() if ctx.injuries_unknown
    }
    blocked = [p for p in game + props if p.event_id in unknown]
    game = [p for p in game if p.event_id not in unknown]
    props = [p for p in props if p.event_id not in unknown]

    soon_cut = now + timedelta(hours=EARLY_HOURS)
    # Only legitimate edges: a market price, a kind of bet that isn't losing blind, a
    # calibrated edge. Value and early plays must also be realistic winners.
    candidates = [p for p in best_per_event(game, settings) if not not_legit(p, settings)]
    soon = [p for p in candidates if p.commence_time <= soon_cut]
    later = [p for p in candidates if p.commence_time > soon_cut]
    chosen: list[tuple[str, Pick]] = []

    def solid(p: Pick) -> bool:
        scan = by_event.get(p.event_id)
        ctx = scan.contexts.get(p.event_id) if scan else None
        if p.warnings or p.market_prob is None or p.model_prob < LOCK_MIN_PROB:
            return False
        if losing_kind(p, settings):
            return False  # the blind study says this kind of bet loses
        if abs(p.model_prob - p.market_prob) > LOCK_MAX_GAP or p.market_prob < LOCK_MIN_MARKET:
            return False
        score, cash, cal_ev = edge_score(p, settings)
        if cash < LOCK_MIN_CASH or score < LOCK_MIN_SCORE or cal_ev <= 0:
            return False
        if scan is None or ctx is None or scan.ratings is None:
            return False
        played = scan.ratings.games_played
        return played.get(ctx.home, 0) >= LOCK_MIN_GAMES and played.get(ctx.away, 0) >= LOCK_MIN_GAMES

    # A Lock and a Dog every morning. A real one (units) must clear every check; when none
    # does, the closest one is still posted as a lean: no units, tracked with the leans so
    # the betting record stays honest. A real one later in the day still posts.
    lean_ids: set[int] = set()

    def lean_pool(extra=lambda p: True, source: list[Pick] | None = None) -> list[Pick]:
        """Today's sides for a lean head pick: a positive edge if any game has one, otherwise
        the best of what is on the board (still a lean: no units), so there is one every day.
        Games with unknown injuries are a last resort."""
        pool = source if source is not None else game + [b for b in blocked if not b.is_prop]
        base = [p for p in pool
                if now < p.commence_time <= soon_cut and not p.warnings and p.american >= settings.max_favorite
                and extra(p)]
        for tier_pool in (
            [p for p in base if p.ev > 0 and p.event_id not in unknown],
            [p for p in base if p.event_id not in unknown],
            base,
        ):
            if tier_pool:
                return tier_pool
        return []
    if "lock" not in taken:
        locks = [p for p in soon if p.american >= LOCK_MIN_ODDS and solid(p)]
        if locks:
            chosen.append(("lock", max(locks, key=lambda p: (p.win_prob, p.ev))))
        elif (settings.fill or settings.daily_lock) and "lock_lean" not in taken:
            # A Lock every day: the most likely winner on today's board (-170 or better, injuries
            # known, our model and the market agreeing first). With no real edge it carries no
            # units, and every daily Lock keeps its own record.
            def agree(p: Pick) -> bool:
                return (p.market_prob is not None and p.market_prob >= 0.5
                        and abs(p.model_prob - p.market_prob) <= LOCK_MAX_GAP)
            near = lean_pool(lambda p: p.market in ("h2h", "spreads") and p.win_prob >= 0.5,
                             source=None if settings.fill else every)
            if near:
                lock = max(near, key=lambda p: (agree(p), p.win_prob, p.ev))
                chosen.append(("lock", lock))
                lean_ids.add(id(lock))
    if "dog" not in taken:
        used_now = {id(c) for _, c in chosen}
        dogs = [p for p in soon if DOG_MIN_ODDS <= p.american <= DOG_MAX_ODDS and id(p) not in used_now]
        if dogs:
            chosen.append(("dog", max(dogs, key=lambda p: p.ev)))
        elif settings.fill and "dog_lean" not in taken:
            near = lean_pool(lambda p: p.market == "h2h" and DOG_MIN_ODDS <= p.american <= DOG_MAX_ODDS
                             and id(p) not in used_now)
            if near:
                dog = max(near, key=lambda p: p.ev)
                chosen.append(("dog", dog))
                lean_ids.add(id(dog))
    used = {id(p) for _, p in chosen}
    chosen += [("value", p) for p in soon if id(p) not in used and p.win_prob >= LEGIT_MIN_WIN][:max_value]
    chosen += [("early", p) for p in later if p.win_prob >= LEGIT_MIN_WIN][:max_early]

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
        and p.american >= settings.max_favorite and p.win_prob >= LEGIT_MIN_WIN
    ] + [
        p for p in blocked
        if not p.is_prop and p.ev > 0 and not p.warnings and p.american >= settings.max_favorite
        and p.win_prob >= LEGIT_MIN_WIN
    ]
    leans = [p for p in leans if p.commence_time <= soon_cut]  # today's slate only, not next week's
    best_lean: dict[str, Pick] = {}
    for p in sorted(leans, key=lambda p: p.ev, reverse=True):
        best_lean.setdefault(p.event_id, p)
    if settings.fill or settings.show_leans:
        chosen += [("lean", p) for p in list(best_lean.values())[:max_leans]]

    # Best bets: today's unit plays ranked by edge score, only those that clear the bar.
    scores = {id(p): edge_score(p, settings) for _, p in chosen}
    slots = max(BEST_BETS - best_taken, 0)
    contenders = [
        p for tier, p in chosen
        if tier in UNIT_TIERS and id(p) not in lean_ids and p.commence_time <= soon_cut and not p.warnings
        and scores[id(p)][0] >= BEST_MIN_SCORE and scores[id(p)][2] > 0
        and not losing_kind(p, settings)  # the studies say this kind of bet loses: not a best bet
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
        if tier in UNIT_TIERS and id(pick) not in lean_ids:
            if line := track_line(pick, settings):
                bullets.append(line)
            if (kind := losing_kind(pick, settings)) and not best_rank.get(id(pick)):
                bullets.append(f"Left out of Top bets: {kind} bets in this sport lost in the blind study "
                               "by more than luck.")
        if tier in UNIT_TIERS and not is_proven(pick, settings):
            bullets.append("Sized at ½ unit: this sport and bet type hasn't beaten the closing line in a blind test yet.")
        if ctx and ctx.injuries_unknown:
            bullets.insert(0, f"No bet: no injury report for {', '.join(ctx.injuries_unknown)}, "
                              "so injuries are unknown. Tracked as a lean only.")
        if id(pick) in lean_ids and tier == "lock":
            bullets.insert(0, f"Today's most likely winner ({pick.win_prob:.0%} on our numbers, the market "
                              f"{pick.market_prob:.0%})" if pick.market_prob is not None else
                              f"Today's most likely winner ({pick.win_prob:.0%} on our numbers)")
        elif id(pick) in lean_ids:
            bullets.insert(0, f"Lean {TIER_LABELS[tier].split(' of ')[0]}: nothing cleared every check today, so this is "
                              "the closest one, posted with no units and tracked with the leans.")
        elif tier == "lock":
            sc, cash, _ = scores[id(pick)]
            bullets.insert(0, f"Passed every Lock check: our model {pick.model_prob:.0%} and the market "
                              f"{pick.market_prob:.0%} both make it a clear favorite and agree, it cashes "
                              f"{cash:.0%} after calibration with a real edge (score {sc:.0f}), injuries are "
                              f"known, -170 or better, and both teams have {LOCK_MIN_GAMES}+ games this season.")
        ctx = scan.contexts.get(pick.event_id) if scan else None
        cards.append(
            Card(
                tier=tier,
                units=units_for(pick, settings) if tier in UNIT_TIERS and id(pick) not in lean_ids else 0.0,
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
