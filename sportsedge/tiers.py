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
LOCK_MAX_ODDS = -120  # and a real favorite: the Lock is a moneyline the team is expected to win
# Spread bets where our model rates a side 6+ points above the market lost 4-7% in every sport
# blind (18,000+ games, 2018+ lines): the market was right. They are never posted, not even as leans.
SPREAD_MAX_OVERREACH = 0.06
# Favorites our model likes 2+ points more than the market lost blind: moneylines -5.2% across six
# sports (NFL -11 to -13%, college football -14 to -27%), football spreads -12% (college). Dogs the
# model liked did better (-3.9% overall, college football +1 to +4%). So a lean never goes to a
# favorite on the model's word alone: that game's lean is the best other side, often the dog.
FAVORITE_OVERREACH = 0.02
FOOTBALL = ("nfl", "ncaaf")
# The Lock of the Day is the safest pick, not the biggest edge, so it must clear every check:
LOCK_MIN_PROB = 0.62  # our model alone gives it at least this
LOCK_MIN_MARKET = 0.55  # and the market (no vig) also has it as a clear favorite
LOCK_MIN_CASH = 0.60  # calibrated chance to cash, after the studies' correction
LOCK_MIN_SCORE = 50  # edge score: a real edge on a likely winner
LOCK_MAX_GAP = 0.06  # model and the no-vig market agree to within 6 points
LOCK_MIN_GAMES = 8  # both teams have enough games this season to trust their ratings
DOG_MIN_ODDS = 100  # the blueprint: dogs +100 to +220 only
DOG_MAX_ODDS = 220
# The owner's rule: a Dog of the Day is a dog we think can actually win, not one we expect to
# lose. Our number has to give it 45%+ (about a coin flip at plus money); otherwise no Dog today.
DOG_MIN_WIN = 0.45
DOG_FLOOR_WIN = 0.40  # the daily fallback: the most likely dog on the board, if it has 40%+
PROP_ODDS_RANGE = (-200, 200)
EARLY_HOURS = 24  # games further out than this are "early value"
# The daily card (owner's rule: picks on the board every day). When fewer than DAILY_MIN picks
# are up for the day, the best remaining sides fill it: every bet rule still applies (-170 max,
# no dog over +300, injuries known, 45%+ to win, a bet type not losing blind, one per game), the
# price is fair on our number (the edge no worse than DAILY_FLOOR_EV, a normal book's margin at
# -110), and they carry no units: tracked as leans so the record shows whether they earn money.
DAILY_MIN = 8
# Owner's call (Oct 16): tighten up. The first 56 no-unit leans went 21-35 (-15.9u at 1u each),
# nearly all of it near-even favorites and totals whose only edge was our ratings, which the
# blind prediction study says add little or nothing to Vegas. So a lean or a daily-card pick
# now has to beat its price with our number trusted only as much as it earned (trusted_ev),
# and never at a negative edge. A short card beats a card of coin flips paying the juice.
DAILY_FLOOR_EV = 0.0
DOG_FALLBACK_FLOOR_EV = -0.045  # the daily Dog fallback (owner: a dog every day), no units
DEFAULT_TRUST = 0.1  # sports and bet types with no prediction study (soccer, tennis, props)
# A lean where our model sits this far from the market is the model missing something, not an
# edge: the first 12 no-unit picks that cleared trusted_ev were NHL totals 8-11 points off the
# market, and they went 2-10.
LEAN_MAX_GAP = 0.06
DAILY_MAX_DOG = 300
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


def overreach(p: Pick) -> bool:
    """A spread where our model sits 6+ points above the market: a proven loser blind."""
    return p.market == "spreads" and p.market_prob is not None and p.model_prob - p.market_prob > SPREAD_MAX_OVERREACH


_STUDY_MARKET = {"h2h": "win", "totals": "total", "spreads": "margin"}


def trusted_ev(p: Pick, settings: Settings | None) -> float:
    """The edge with our number trusted only as much as the blind prediction study says it earned:
    the consensus no-vig chance moved toward our model by that weight, against the price."""
    if p.market_prob is None or p.is_prop:
        return p.ev
    if abs(p.model_prob - p.market_prob) > LEAN_MAX_GAP:
        return -1.0  # too far from the market to trust
    w = (settings.model_trust.get(f"{p.sport}:{p.market}", DEFAULT_TRUST)
         if settings is not None and settings.model_trust else DEFAULT_TRUST)
    q = p.market_prob + w * (p.model_prob - p.market_prob)
    dec = 1 + p.american / 100 if p.american > 0 else 1 + 100 / -p.american
    return q * dec - 1


def model_trust(study: dict | None) -> dict[str, float]:
    """forecast_study.json -> "sport:market" model weights for trusted_ev."""
    out = {}
    for sport, r in (study or {}).items():
        for market, key in _STUDY_MARKET.items():
            w = (r.get("weights") or {}).get(key)
            if w is not None:
                out[f"{sport}:{market}"] = float(w)
    return out


def favorite_overreach(p: Pick) -> bool:
    """A favorite whose edge is only our model liking it 2+ points more than the market."""
    if p.market_prob is None or p.model_prob - p.market_prob < FAVORITE_OVERREACH:
        return False
    if p.market == "h2h":
        return p.american < 0
    return p.market == "spreads" and p.sport in FOOTBALL and p.point is not None and p.point < 0


def is_lock_price(p: Pick) -> bool:
    """A Lock is a moneyline favorite from -170 to -120 (it wins 55-62% of the time at those
    prices), never a spread or total: those are built to be coin flips."""
    if p.sport in FOOTBALL and favorite_overreach(p):
        return False  # football favorites on the model's word alone lost 11-27% blind
    return p.market == "h2h" and not p.is_prop and LOCK_MIN_ODDS <= p.american <= LOCK_MAX_ODDS


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
    posted_today: int | None = None,
    daily_min: int = DAILY_MIN,
    posted_events: set[str] | None = None,
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
        every.extend(p for p in scan.picks if not p.is_prop and not overreach(p))
        # Only bets backed by a blind test: no maybes on the board.
        game.extend(p for p in scan.picks if not p.is_prop and backed(p, settings) and not overreach(p))
        props.extend(p for p in scan.picks if p.is_prop and backed(p, settings))

    # No bet on a game where either team's injury report is unknown; those picks can
    # still show as leans (no units), so they are tracked.
    unknown = {
        ev_id for scan in scans for ev_id, ctx in scan.contexts.items() if ctx.injuries_unknown
    }
    blocked = [p for p in game + props if p.event_id in unknown]
    # The owner's rule: injuries unknown, no bet. Those games post nothing at all (not even a lean).
    every = [p for p in every if p.event_id not in unknown]
    blocked = []
    game = [p for p in game if p.event_id not in unknown]
    props = [p for p in props if p.event_id not in unknown]

    soon_cut = now + timedelta(hours=EARLY_HOURS)
    # Only legitimate edges: a market price, a kind of bet that isn't losing blind, a
    # calibrated edge. Value and early plays must also be realistic winners.
    def spots_of(p: Pick) -> list[str]:
        scan = by_event.get(p.event_id)
        ctx = scan.contexts.get(p.event_id) if scan else None
        if ctx and p.market == "h2h" and is_dog_price(p.american):
            return list(ctx.spots.get(p.side, []))
        if ctx and p.market == "totals" and p.side == "under":
            return list(ctx.spots.get("under", []))
        return []

    def backed_edge(p: Pick) -> float:
        """The edge the data backs (owner, Oct 16: value, early and best bets off studies and data).
        A proven bet type or proven spot keeps its full edge: that edge beat the closing line blind.
        Anything else counts our number only as much as the prediction study says it earned."""
        if (settings.proven is not None and is_proven(p, settings)) \
                or any(f"{p.sport}:{k}" in settings.spot_weights for k in spots_of(p)):
            return p.ev
        return trusted_ev(p, settings)

    candidates = [p for p in best_per_event(game, settings) if not not_legit(p, settings)]
    if settings.model_trust:  # the prediction study is loaded (live board; demo and tests skip it)
        candidates = [p for p in candidates if backed_edge(p) > 0]
    soon = [p for p in candidates if p.commence_time <= soon_cut]
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
    live_dog_ids: set[int] = set()

    def lean_pool(extra=lambda p: True, source: list[Pick] | None = None) -> list[Pick]:
        """Today's sides for a lean head pick: a positive edge if any game has one, otherwise
        (fill mode only) the best of what is on the board. The daily Lock and Dog never take a
        side that loses to its price: it must beat break-even (EV > 0). Games with unknown
        injuries are a last resort."""
        pool = source if source is not None else game + [b for b in blocked if not b.is_prop]
        base = [p for p in pool
                if now < p.commence_time <= soon_cut and not p.warnings and p.american >= settings.max_favorite
                and p.market_prob is not None  # no market price, no way to check the edge: never a Lock/Dog
                and extra(p)]
        tiers = [[p for p in base if p.ev > 0 and p.event_id not in unknown], [p for p in base if p.ev > 0]]
        if settings.fill:
            tiers += [[p for p in base if p.event_id not in unknown], base]
        for tier_pool in tiers:
            if tier_pool:
                return tier_pool
        return []
    heads = settings.fill or settings.daily_heads
    if "lock" not in taken:
        locks = [p for p in soon if is_lock_price(p) and solid(p)]
        if locks:
            # Best edge AND likely to win (the edge score), not just the biggest favorite.
            chosen.append(("lock", max(locks, key=lambda p: (edge_score(p, settings)[0], p.win_prob))))
        elif (heads or settings.daily_lock) and "lock_lean" not in taken:
            # A Lock every day: the most likely winner on today's board (-170 or better, injuries
            # known, our model and the market agreeing first). With no real edge it carries no
            # units, and every daily Lock keeps its own record.
            def agree(p: Pick) -> bool:
                return (p.market_prob is not None and p.market_prob >= 0.5
                        and abs(p.model_prob - p.market_prob) <= LOCK_MAX_GAP)
            near = lean_pool(lambda p: is_lock_price(p) and p.win_prob >= 0.5, source=None if heads else every)
            if near:
                # Weigh everything (the edge score blends the edge with the chance to win),
                # not just who is favored.
                lock = max(near, key=lambda p: (agree(p), edge_score(p, settings)[0], p.win_prob))
                chosen.append(("lock", lock))
                lean_ids.add(id(lock))
    if "dog" not in taken:
        used_now = {id(c) for _, c in chosen}
        dogs = [p for p in soon if DOG_MIN_ODDS <= p.american <= DOG_MAX_ODDS and id(p) not in used_now
                and p.win_prob >= DOG_MIN_WIN]
        if dogs:
            chosen.append(("dog", max(dogs, key=lambda p: p.ev)))
        elif heads and "dog_lean" not in taken:
            near = lean_pool(lambda p: p.market == "h2h" and DOG_MIN_ODDS <= p.american <= DOG_MAX_ODDS
                             and id(p) not in used_now and p.win_prob >= DOG_MIN_WIN)
            if near:
                dog = max(near, key=lambda p: p.ev)
                chosen.append(("dog", dog))
                lean_ids.add(id(dog))
            # No fallback Dog (owner, Oct 23: "Why would we put a dog bet out and then say no juice
            # here?"). The Dog of the Day posts only when a 45%+ dog beats its price; otherwise none.
    used = {id(p) for _, p in chosen}
    chosen += [("value", p) for p in soon if id(p) not in used and p.win_prob >= LEGIT_MIN_WIN][:max_value]
    # Early: any game more than a day out where our number beats the price (a real edge, however
    # small) on a realistic winner, so the line can be taken before it moves. Best side per game.
    early_pool = [
        p for p in game
        if p.commence_time > soon_cut and p.ev > 0 and not p.warnings and p.american >= settings.max_favorite
        and p.win_prob >= LEGIT_MIN_WIN and not favorite_overreach(p) and not not_legit(p, settings)
        and (not settings.model_trust or backed_edge(p) > 0)
    ]
    best_early: dict[str, Pick] = {}
    for p in sorted(early_pool, key=lambda p: p.ev, reverse=True):
        if id(p) not in used:
            best_early.setdefault(p.event_id, p)
    chosen += [("early", p) for p in list(best_early.values())[:max_early]]

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
        and p.american >= settings.max_favorite and p.win_prob >= LEGIT_MIN_WIN and not favorite_overreach(p)
    ] + [
        p for p in blocked
        if not p.is_prop and p.ev > 0 and not p.warnings and p.american >= settings.max_favorite
        and p.win_prob >= LEGIT_MIN_WIN and not favorite_overreach(p)
    ]
    leans = [p for p in leans if trusted_ev(p, settings) > 0]
    leans = [p for p in leans if p.commence_time <= soon_cut]  # today's slate only, not next week's
    best_lean: dict[str, Pick] = {}
    for p in sorted(leans, key=lambda p: p.ev, reverse=True):
        best_lean.setdefault(p.event_id, p)
    if settings.fill or settings.show_leans:
        chosen += [("lean", p) for p in list(best_lean.values())[:max_leans]]

    # The daily card: at least ``daily_min`` picks on the board each day (see DAILY_MIN).
    card_ids: set[int] = set()
    if posted_today is not None:
        have = posted_today + sum(1 for _, p in chosen if p.commence_time <= soon_cut)
        covered = {p.event_id for _, p in chosen} | set(posted_events or ())
        fill = [
            p for p in game
            if p.event_id not in covered and p.commence_time <= soon_cut and not p.warnings
            and settings.max_favorite <= p.american <= DAILY_MAX_DOG and p.win_prob >= LEGIT_MIN_WIN
            and p.ev >= DAILY_FLOOR_EV and p.market_prob is not None and not favorite_overreach(p)
            and trusted_ev(p, settings) > DAILY_FLOOR_EV and not losing_kind(p, settings)
        ]
        best_fill: dict[str, Pick] = {}
        for p in sorted(fill, key=lambda p: (p.ev, p.win_prob), reverse=True):
            best_fill.setdefault(p.event_id, p)
        for p in list(best_fill.values())[:max(daily_min - have, 0)]:
            chosen.append(("lean", p))
            card_ids.add(id(p))
        from collections import Counter

        why = Counter()
        for p in every:
            if p.commence_time > soon_cut or p.event_id in covered:
                continue
            why[(p.sport, "unknown injuries" if p.event_id in unknown else "warning" if p.warnings
                 else "odds" if not settings.max_favorite <= p.american <= DAILY_MAX_DOG
                 else "under 45%" if p.win_prob < LEGIT_MIN_WIN else "edge below floor" if p.ev < DAILY_FLOOR_EV
                 else "no edge once Vegas is trusted" if trusted_ev(p, settings) <= DAILY_FLOOR_EV
                 else "no market" if p.market_prob is None else "favorite overreach" if favorite_overreach(p)
                 else "losing kind" if losing_kind(p, settings) else "not backed" if not backed(p, settings)
                 else "ok")] += 1
        print(f"daily card: {have} picks up for the next {EARLY_HOURS}h, {len(best_fill)} games eligible, "
              f"{len(card_ids)} added; sides by rule: " + ", ".join(f"{k[0]} {k[1]} {v}" for k, v in sorted(why.items())))

    # Best bets: today's unit plays ranked by edge score, only those that clear the bar.
    scores = {id(p): edge_score(p, settings) for _, p in chosen}
    slots = max(BEST_BETS - best_taken, 0)
    contenders = [
        p for tier, p in chosen
        if tier in UNIT_TIERS and id(p) not in lean_ids and p.commence_time <= soon_cut and not p.warnings
        and scores[id(p)][0] >= BEST_MIN_SCORE and scores[id(p)][2] > 0
        and (not settings.model_trust or backed_edge(p) > 0)
        and not losing_kind(p, settings)  # the studies say this kind of bet loses: not a best bet
    ]
    ranked = sorted(contenders, key=lambda p: scores[id(p)][0], reverse=True)[:slots]
    best_rank = {id(p): best_taken + i + 1 for i, p in enumerate(ranked)}

    def earns_units(pick: Pick, spots: list[str]) -> bool:
        """Proven blind: its sport and bet type, or a spot it is in (e.g. the NFL windy under)."""
        return is_proven(pick, settings) or any(f"{pick.sport}:{k}" in settings.spot_weights for k in spots)

    cards = []
    no_units: set[int] = set()
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
        # The owner's rule: the Lock is never a lean. Unproven, it carries a small stake, but only
        # if it passes every bet rule; otherwise it stays a no-unit lean as before.
        head_units = settings.lock_units if tier == "lock" else settings.dog_units if tier == "dog" else 0.0
        small_lock = (head_units > 0 and not earns_units(pick, in_spots)
                      and pick.ev > 0 and pick.american >= LOCK_MIN_ODDS and not pick.warnings
                      and pick.market_prob is not None  # a real edge needs a market price to check against
                      and (tier == "lock" or (pick.american > 0 and pick.win_prob >= DOG_MIN_WIN))
                      and not (ctx and ctx.injuries_unknown))
        if small_lock:
            fallback = id(pick) in lean_ids
            lean_ids.discard(id(pick))
            best_rank.pop(id(pick), None)  # unproven: not a Top bet
            bullets.insert(0, f"Small stake ({head_units:g}u): today's {'Lock' if tier == 'lock' else 'Dog'} gets one "
                              "when it beats its price. This bet type hasn't beaten the closing line in a blind test "
                              "yet, so it stays small.")
            if fallback:
                bullets.insert(0, f"Today's best Lock: the strongest side on the board ({pick.win_prob:.0%} on our "
                                  f"numbers, {pick.ev:+.1%} edge), -170 or better, injuries known.")
        elif settings.units_proven_only and tier in UNIT_TIERS and id(pick) not in lean_ids \
                and not earns_units(pick, in_spots):
            # Money only on proven bet types: this one posts with no units until it proves out.
            bullets.insert(0, "No units: this sport and bet type hasn't beaten the closing line in a blind "
                              "test yet, so we're riding it as a lean until it proves itself.")
            if tier in ("lock", "dog"):
                lean_ids.add(id(pick))  # still today's Lock/Dog, with no units
            elif tier == "early":
                no_units.add(id(pick))  # still early value (bet it before the line moves), with no units
            else:
                tier = "lean"
            best_rank.pop(id(pick), None)
        elif tier in UNIT_TIERS and not is_proven(pick, settings):
            bullets.append("Sized at ½ unit: this sport and bet type hasn't beaten the closing line in a blind test yet.")
        if ctx and ctx.injuries_unknown:
            bullets.insert(0, f"No bet: no injury report for {', '.join(ctx.injuries_unknown)}, "
                              "so injuries are unknown. Tracked as a lean only.")
        if id(pick) in card_ids:
            bullets.insert(0, f"Daily card: one of the best numbers on the board today. We got it "
                              f"{pick.win_prob:.0%} to win, and at {pick.american:+.0f} the edge is {pick.ev:+.1%}: "
                              + ("a little edge" if pick.ev > 0 else "priced about right, books still take their cut")
                              + ". No units on it, just a play we're tracking.")
        elif id(pick) in lean_ids and tier == "lock":
            bullets.insert(0, f"Today's most likely winner ({pick.win_prob:.0%} on our numbers, the market "
                              f"{pick.market_prob:.0%})" if pick.market_prob is not None else
                              f"Today's most likely winner ({pick.win_prob:.0%} on our numbers)")
        elif id(pick) in live_dog_ids:
            bullets.insert(0, f"Best live dog on the board: we got em winning outright {pick.win_prob:.0%} of the time, "
                              f"the most of any dog today, and {pick.american:+.0f} pays fair for that. No units, "
                              "it's a shot, not a lock.")
        elif id(pick) in lean_ids:
            bullets.insert(0, f"Lean {TIER_LABELS[tier].split(' of ')[0]}: nothing cleared every check today, so this is "
                              "the closest one, posted with no units and tracked with the leans.")
        elif tier == "lock" and not small_lock:
            sc, cash, _ = scores[id(pick)]
            bullets.insert(0, f"Passed every Lock check: our model {pick.model_prob:.0%} and the market "
                              f"{pick.market_prob:.0%} both make it a clear favorite and agree, it cashes "
                              f"{cash:.0%} after calibration with a real edge (score {sc:.0f}), injuries are "
                              f"known, -170 or better, and both teams have {LOCK_MIN_GAMES}+ games this season.")
        ctx = scan.contexts.get(pick.event_id) if scan else None
        cards.append(
            Card(
                tier=tier,
                units=(head_units if small_lock
                       else units_for(pick, settings)
                       if tier in UNIT_TIERS and id(pick) not in lean_ids and id(pick) not in no_units else 0.0),
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
