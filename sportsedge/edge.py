"""Compare model probabilities with bookmaker prices and rank the edges."""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime
from statistics import mean
from typing import Callable, Iterable

from .data.base import Event, Quote
from .odds import american_to_decimal, devig, expected_value, implied_prob, kelly_fraction, prob_to_american

GAME_MARKET_LABELS = {"h2h": "Moneyline", "spreads": "Spread", "totals": "Total"}
DEFAULT_SHARP_BOOKS = ("pinnacle", "circasports", "lowvig", "betonlineag", "matchbook", "betfair_ex_eu")

# (win, push, loss) for one side of one line
Outcome = tuple[float, float, float]
ModelFn = Callable[[str, float | None], Outcome | None]


@dataclass
class Settings:
    min_ev: float = 0.02
    market_weight: float = 0.75  # 0 = trust the model only, 1 = trust the market only
    prop_market_weight: float = 0.8  # props: player samples are small, lean on the market more
    weight_overrides: dict[str, float] = field(default_factory=dict)  # "sport:game"/"sport:prop" -> learned weight
    min_ev_overrides: dict[str, float] = field(default_factory=dict)  # sport -> EV threshold chosen by the studies
    calibration: dict[str, tuple[float, float]] = field(default_factory=dict)  # sport -> Platt (a, b) from the studies
    kelly_multiplier: float = 0.25
    max_stake: float = 0.03  # cap on fraction of bankroll per bet
    bankroll: float | None = None
    devig_method: str = "power"
    sharp_books: tuple[str, ...] = DEFAULT_SHARP_BOOKS
    disagreement_warning: float = 0.12
    hide_flagged: bool = True  # leave out picks where model and market disagree by > disagreement_warning
    max_odds: float = 600  # skip extreme longshots (e.g. +1500 props)
    max_ev: float = 0.20  # EV above this is almost always stale data / news the model can't see
    max_favorite: float = -150  # never bet a price worse than this: big favorites kill ROI
    spot_weights: dict[str, float] = field(default_factory=dict)  # "sport:spot" -> win-chance bump, proven spots only
    proven: set[str] | None = None  # "sport:market" proven blind; None = no studies loaded (no sizing cap)
    # Blind study records: "sport:moneyline" / "sport:coin flips (-149 to +149)" ->
    # {"bets", "won", "lost", "roi", "se"}. Best bets skip a kind of bet that lost beyond luck.
    track: dict[str, dict] = field(default_factory=dict)
    # No-unit posts (a daily Lock/Dog when nothing qualifies, leans). Off: only backed bets are posted.
    fill: bool = False
    # A Lock every day: when nothing backed passes the Lock checks, the day's most likely winner
    # is still posted as the Lock, with no units, clearly labeled and tracked in its own record.
    daily_lock: bool = True
    # Adaptive trust from our own picks' closing line value: "sport:market" -> stake factor.
    # 0 = paused (keeps losing to the close), 0.5 = cut, 1.25 = picks keep beating the close.
    clv_trust: dict[str, float] = field(default_factory=dict)

    def min_ev_for(self, pick: "Pick") -> float:
        if pick.is_prop:
            return self.min_ev
        return max(self.min_ev_overrides.get(pick.sport, self.min_ev), 0.0)

    def weight_for(self, sport: str, is_prop: bool) -> float:
        key = f"{sport}:{'prop' if is_prop else 'game'}"
        if key in self.weight_overrides:
            return self.weight_overrides[key]
        return self.prop_market_weight if is_prop else self.market_weight


@dataclass
class Pick:
    sport: str
    event_id: str
    commence_time: datetime
    matchup: str
    market: str
    market_label: str
    selection: str
    side: str
    point: float | None
    player: str | None
    book: str
    american: float
    model_prob: float  # P(win | no push) from the model alone
    market_prob: float | None  # consensus no-vig P(win | no push)
    win_prob: float  # blended, used for EV
    push_prob: float
    fair_american: float
    ev: float
    kelly: float
    stake: float | None
    projection: str
    books: int
    warnings: list[str] = field(default_factory=list)

    @property
    def is_prop(self) -> bool:
        return self.player is not None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["commence_time"] = self.commence_time.isoformat()
        return d


def _line_key(q: Quote) -> float | None:
    if q.point is None:
        return None
    # Spreads: express both sides from the home team's perspective.
    if q.market == "spreads" and q.side == "away":
        return -q.point
    return q.point


def group_quotes(quotes: Iterable[Quote]) -> dict[tuple, list[Quote]]:
    groups: dict[tuple, list[Quote]] = defaultdict(list)
    for q in quotes:
        groups[(q.market, q.player, _line_key(q))].append(q)
    return groups


def consensus_no_vig(quotes: list[Quote], n_sides: int, method: str, sharp_books: tuple[str, ...]) -> dict[str, float]:
    """No-vig probability per side, preferring sharp books when present."""
    by_book: dict[str, dict[str, Quote]] = defaultdict(dict)
    for q in quotes:
        by_book[q.book][q.side] = q
    per_book: dict[str, dict[str, float]] = {}
    for book, sides in by_book.items():
        if len(sides) != n_sides:
            continue
        names = list(sides)
        fair = devig([implied_prob(sides[s].american) for s in names], method)
        per_book[book] = dict(zip(names, fair))
    if not per_book:
        return {}
    sharp = [b for b in per_book if b in sharp_books]
    use = sharp or list(per_book)
    sides = per_book[use[0]].keys()
    return {s: mean(per_book[b][s] for b in use if s in per_book[b]) for s in sides}


def evaluate(
    event: Event,
    quotes: list[Quote],
    model: Callable[[Quote], Outcome | None],
    describe: Callable[[Quote], tuple[str, str]],
    settings: Settings,
    n_sides: Callable[[str], int],
) -> list[Pick]:
    """Price every side of every line in ``quotes`` against ``model``.

    ``model(quote)`` returns (win, push, loss); ``describe(quote)`` returns the
    selection text and a short projection string.
    """
    picks: list[Pick] = []
    matchup = f"{event.away} @ {event.home}"
    for (market, player, _), group in group_quotes(quotes).items():
        market_probs = consensus_no_vig(group, n_sides(market), settings.devig_method, settings.sharp_books)
        books = len({q.book for q in group})
        by_side: dict[tuple[str, float | None], list[Quote]] = defaultdict(list)
        for q in group:
            by_side[(q.side, q.point)].append(q)
        for (side, point), side_quotes in by_side.items():
            best = max(side_quotes, key=lambda q: american_to_decimal(q.american))
            if best.american > settings.max_odds:
                continue
            probs = model(best)
            if probs is None:
                continue
            pw, pp, pl = probs
            if pw + pl <= 0:
                continue
            q_model = pw / (pw + pl)
            q_mkt = market_probs.get(side)
            w = settings.weight_for(event.sport, player is not None)
            q = q_model if q_mkt is None else (1 - w) * q_model + w * q_mkt
            q = min(max(q, 1e-4), 1 - 1e-4)
            win, loss = q * (1 - pp), (1 - q) * (1 - pp)
            dec = american_to_decimal(best.american)
            ev = expected_value(win, loss, dec)
            kelly = min(kelly_fraction(win, loss, dec) * settings.kelly_multiplier, settings.max_stake)
            warnings = []
            if q_mkt is not None and abs(q_model - q_mkt) > settings.disagreement_warning:
                warnings.append(
                    f"model {q_model:.0%} vs market {q_mkt:.0%}: check injuries/lineups before betting"
                )
            selection, projection = describe(best)
            label = GAME_MARKET_LABELS.get(market, market.replace("player_", "").replace("batter_", "").replace("pitcher_", "pitcher ").replace("_", " ").title())
            picks.append(
                Pick(
                    sport=event.sport,
                    event_id=event.id,
                    commence_time=event.commence_time,
                    matchup=matchup,
                    market=market,
                    market_label=label,
                    selection=selection,
                    side=side,
                    point=point,
                    player=player,
                    book=best.book,
                    american=best.american,
                    model_prob=q_model,
                    market_prob=q_mkt,
                    win_prob=q,
                    push_prob=pp,
                    fair_american=prob_to_american(q),
                    ev=ev,
                    kelly=kelly,
                    stake=round(kelly * settings.bankroll, 2) if settings.bankroll else None,
                    projection=projection,
                    books=books,
                    warnings=warnings,
                )
            )
    return picks


def qualifying(picks: list[Pick], settings: Settings) -> list[Pick]:
    """Picks clearing the EV threshold and the sanity cap, best first."""
    out = [
        p for p in picks
        if settings.min_ev_for(p) <= p.ev <= settings.max_ev and not (settings.hide_flagged and p.warnings)
        and p.american >= settings.max_favorite
    ]
    return sorted(out, key=lambda p: p.ev, reverse=True)


def growth(p: Pick) -> float:
    """Expected log-bankroll growth per bet at full Kelly.

    Ranks bets by edge *and* certainty: a +4% EV bet at -110 beats a +6% EV
    longshot at +450 because it grows a bankroll faster with less variance.
    """
    b = american_to_decimal(p.american) - 1
    win = p.win_prob * (1 - p.push_prob)
    loss = (1 - p.win_prob) * (1 - p.push_prob)
    f = kelly_fraction(win, loss, b + 1)
    if f <= 0:
        return 0.0
    return win * math.log(1 + b * f) + loss * math.log(1 - f)


def edge_score(p: Pick, settings: Settings) -> tuple[float, float, float]:
    """(score 0-100, calibrated win prob, calibrated EV).

    The win probability is first corrected by what the studies measured (when we
    said 60%, how often did it really win?), then the score weighs edge (60 points:
    calibrated EV, full marks at +10%) and how likely it is to cash (40 points:
    full marks at 75%). Edge points shrink for bets that cash less than 45% of the
    time, so a long shot's big EV can't outrank a likely winner. Big edge on a
    likely winner scores highest.
    """
    q = p.win_prob
    platt = None if p.is_prop else settings.calibration.get(p.sport)
    if platt:
        a, b = platt
        x = math.log(min(max(q, 1e-4), 1 - 1e-4) / (1 - min(max(q, 1e-4), 1 - 1e-4)))
        q = 1 / (1 + math.exp(-(a + b * x)))
    dec = american_to_decimal(p.american)
    ev = q * (1 - p.push_prob) * (dec - 1) - (1 - q) * (1 - p.push_prob)
    edge_pts = 60 * min(max(ev / 0.10, 0.0), 1.0) * min(q / 0.45, 1.0)
    sure_pts = 40 * min(max((q - 0.40) / 0.35, 0.0), 1.0)
    return round(edge_pts + sure_pts, 1), q, ev


def best_per_event(picks: list[Pick], settings: Settings) -> list[Pick]:
    """The single highest-EV pick per event (game lines and props separately)."""
    best: dict[tuple[str, bool], Pick] = {}
    for p in qualifying(picks, settings):
        key = (p.event_id, p.is_prop)
        if key not in best or p.ev > best[key].ev:
            best[key] = p
    return sorted(best.values(), key=lambda p: p.ev, reverse=True)


def fmt_point(point: float | None, signed: bool = True) -> str:
    if point is None:
        return ""
    s = f"{point:+g}" if signed else f"{point:g}"
    return s
