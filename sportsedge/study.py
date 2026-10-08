"""Studies: replay past games against their real closing odds and learn how to weigh picks.

For every finished game in the study window the model is refitted using only
games played before that week (no peeking), and every side of the closing
DraftKings moneyline, spread and total is priced the way the live engine
prices it. Each side is then graded against the final score. On that record
the studies measure:

- market weight: how much to trust the market's no-vig price vs our model,
- minimum edge: which EV threshold actually made money,
- bet type and price band: where the edges held up (spreads, totals, ML;
  favorites, coin flips, underdogs),
- calibration: when we said 60%, how often it really won (Platt scaling),
  which turns a raw win probability into an honest one for the edge score.

Closing lines are the sharpest prices of the day, so this is a hard test:
an edge that survives it is worth trusting.
"""

from __future__ import annotations

import logging
import math
from collections import defaultdict
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timedelta
from typing import Callable

from .brain import with_shapes
from .data.base import Event, Quote
from .edge import Settings
from .engine import Engine
from .models.game import predict_game
from .models.ratings import GameResult, fit_ratings, is_b2b, rest_days
from .odds import american_to_decimal
from .sports import SportConfig

log = logging.getLogger(__name__)

WEIGHTS = (0.5, 0.6, 0.7, 0.75, 0.8, 0.85, 0.9, 0.95)
MIN_EVS = (0.0, 0.01, 0.02, 0.03, 0.05, 0.08)
MIN_BETS = 30  # a setting needs at least this many bets before its result counts
PROVEN_BETS = 60  # and a sport needs this many before it counts as beating the close
STUDY_BOOK = "draftkings"
CHECK_VERSION = "checked-v2"
OPEN_BOOK = "draftkings_open"
MIN_CALIBRATION_SIDES = 300
STUDY_DAYS = 730  # about two seasons: judge a sport on thousands of games, never a few nights
BLIND_SHARE = 0.4
MAX_FAVORITE = -150  # the newest 40% of games are kept blind: settings are tuned on the older 60% only
DEFAULT_WEIGHT = 0.75
# Closing lines are sharper than the lines we actually bet hours earlier, so the
# weight they favor is an upper bound on how much to trust the market. Keep the
# chosen weight in a range that still lets the model find edges on earlier lines.
WEIGHT_RANGE = (0.6, 0.8)


@dataclass
class Side:
    """One side of one closing line, priced by the model and graded."""
    sport: str
    event_id: str
    market: str  # h2h / spreads / totals
    side: str
    american: float
    model_prob: float  # P(win | no push), model only
    market_prob: float | None  # closing no-vig P(win | no push)
    push_prob: float
    win: float  # graded (win, push, loss) shares; quarter lines split
    push: float
    loss: float
    date: str = ""  # game start (ISO), for splitting tuning games from blind ones
    point: float | None = None

    def blended(self, w: float) -> float:
        if self.market_prob is None:
            return self.model_prob
        return min(max((1 - w) * self.model_prob + w * self.market_prob, 1e-4), 1 - 1e-4)

    def ev(self, w: float) -> float:
        q = self.blended(w)
        dec = american_to_decimal(self.american)
        return q * (1 - self.push_prob) * (dec - 1) - (1 - q) * (1 - self.push_prob)

    @property
    def profit(self) -> float:
        """Profit of a flat 1-unit bet."""
        return self.win * (american_to_decimal(self.american) - 1) - self.loss


@dataclass
class Result:
    label: str
    bets: int = 0
    won: float = 0.0
    lost: float = 0.0
    units: float = 0.0
    roi: float | None = None
    se: float | None = None  # standard error of the ROI

    @property
    def score(self) -> float:
        """Conservative ROI: the result minus one standard error (luck)."""
        if self.roi is None or self.bets < MIN_BETS:
            return -math.inf
        return self.roi - (self.se or 0.0)


def summarize(label: str, bets: list[Side]) -> Result:
    r = Result(label, bets=len(bets))
    if not bets:
        return r
    profits = [b.profit for b in bets]
    r.won = sum(b.win for b in bets)
    r.lost = sum(b.loss for b in bets)
    r.units = sum(profits)
    r.roi = r.units / len(bets)
    if len(bets) > 1:
        var = sum((p - r.roi) ** 2 for p in profits) / (len(bets) - 1)
        r.se = math.sqrt(var / len(bets))
    return r


def select(sides: list[Side], w: float, min_ev: float, max_ev: float = 0.20, gap: float = 0.12) -> list[Side]:
    """The bets the engine would have made: best qualifying side per game."""
    best: dict[str, tuple[float, Side]] = {}
    for s in sides:
        if s.american < MAX_FAVORITE:
            continue  # never bet worse than -150, in the backtest as live
        if s.market_prob is not None and abs(s.model_prob - s.market_prob) > gap:
            continue  # the live engine hides these too
        ev = s.ev(w)
        if not (min_ev <= ev <= max_ev):
            continue
        if s.event_id not in best or ev > best[s.event_id][0]:
            best[s.event_id] = (ev, s)
    return [s for _, s in best.values()]


def band(american: float) -> str:
    if american <= -150:
        return "favorites (-150 or shorter)"
    if american < 150:
        return "coin flips (-149 to +149)"
    return "underdogs (+150 or longer)"


# ------------------------------------------------------------------ calibration
def fit_platt(xs: list[float], ys: list[float], ridge: float = 1.0, iters: int = 50) -> tuple[float, float]:
    """Fit P(win) = sigmoid(a + b * logit(p)). Starts from (0, 1), i.e. 'trust p as is',
    and the ridge pulls toward that, so a small sample barely moves it."""
    a, b = 0.0, 1.0
    for _ in range(iters):
        ga = gb = 0.0
        haa = hab = hbb = 0.0
        for x, y in zip(xs, ys):
            p = 1 / (1 + math.exp(-(a + b * x)))
            ga += p - y
            gb += (p - y) * x
            w = p * (1 - p)
            haa += w
            hab += w * x
            hbb += w * x * x
        ga += ridge * a
        gb += ridge * (b - 1)
        haa += ridge
        hbb += ridge
        det = haa * hbb - hab * hab
        if det <= 1e-12:
            break
        da = (hbb * ga - hab * gb) / det
        db = (haa * gb - hab * ga) / det
        a, b = a - da, b - db
        if abs(da) < 1e-9 and abs(db) < 1e-9:
            break
    return a, b


def logit(p: float) -> float:
    p = min(max(p, 1e-4), 1 - 1e-4)
    return math.log(p / (1 - p))


def calibrate(p: float, platt: tuple[float, float] | None) -> float:
    if not platt:
        return p
    a, b = platt
    return 1 / (1 + math.exp(-(a + b * logit(p))))


# ---------------------------------------------------------------------- report
@dataclass
class SportStudy:
    sport: str
    name: str
    games: int = 0
    sides: int = 0
    window: str = ""
    weight: float = DEFAULT_WEIGHT  # chosen market weight
    min_ev: float = 0.02  # chosen EV threshold
    proven: bool = False  # the blind test beat the closing line by more than luck over 60+ bets
    tune_window: str = ""
    blind_window: str = ""
    in_sample: Result | None = None  # chosen settings on the games they were tuned on
    blind: Result | None = None  # the same settings on newer games they never saw
    early_in_sample: Result | None = None  # betting the opening line instead of the close
    early_blind: Result | None = None
    early_clv: float | None = None  # average value of the opening price against the closing no-vig line
    spots: list[dict] = field(default_factory=list)  # one tracked record per early-bet spot (see spots.py)
    carryover: float = 1.0  # weight of last season's games, chosen on the tuning games
    carryover_test: list[tuple] = field(default_factory=list)  # (carryover, tuned log loss, games, blind log loss, games)
    weight_log_loss: list[tuple[float, float]] = field(default_factory=list)  # (weight, log loss per side)
    platt: tuple[float, float] | None = None
    best: Result | None = None  # the chosen setting's record
    by_weight: list[Result] = field(default_factory=list)
    by_min_ev: list[Result] = field(default_factory=list)
    by_market: list[Result] = field(default_factory=list)
    by_band: list[Result] = field(default_factory=list)
    calibration: list[tuple[float, float, int]] = field(default_factory=list)  # (said, won, n)
    note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "SportStudy":
        d = dict(d)
        for key in ("by_weight", "by_min_ev", "by_market", "by_band"):
            d[key] = [Result(**r) for r in d.get(key, [])]
        for key in ("best", "in_sample", "blind", "early_in_sample", "early_blind"):
            if d.get(key):
                d[key] = Result(**d[key])
        if d.get("platt"):
            d["platt"] = tuple(d["platt"])
        d["calibration"] = [tuple(c) for c in d.get("calibration", [])]
        d["weight_log_loss"] = [tuple(c) for c in d.get("weight_log_loss", [])]
        d["carryover_test"] = [tuple(c) for c in d.get("carryover_test", [])]
        return cls(**d)


def split_blind(sides: list[Side]) -> tuple[list[Side], list[Side]]:
    """(tuning, blind): whole games, split by start time, newest BLIND_SHARE kept blind."""
    dates = sorted({(s.date, s.event_id) for s in sides})
    if not dates:
        return [], []
    cut = dates[int(len(dates) * (1 - BLIND_SHARE))][0] if len(dates) > 1 else dates[-1][0] + "~"
    return [s for s in sides if s.date < cut], [s for s in sides if s.date >= cut]


def _window(sides: list[Side]) -> str:
    dates = [s.date for s in sides if s.date]
    if not dates:
        return ""
    lo, hi = min(dates), max(dates)
    fmt = lambda iso: datetime.fromisoformat(iso).strftime("%b %-d, %Y")  # noqa: E731
    return f"{fmt(lo)} - {fmt(hi)}"


def study_sides(sides: list[Side], cfg: SportConfig, games: int, window: str,
                open_sides: list[Side] | None = None) -> SportStudy:
    """Tune on the older games, then judge the same settings on newer games they never saw."""
    st = SportStudy(cfg.key, cfg.name, games=games, sides=len(sides), window=window)
    if not sides:
        st.note = "No finished games with closing odds in the study window yet."
        return st
    tune, blind = split_blind(sides)
    st.tune_window, st.blind_window = _window(tune), _window(blind)

    # 1. Market weight, on the tuning games only: the blend whose probabilities best
    # predicted what happened (log loss, a proper score; far steadier than ROI).
    graded = [s for s in tune if s.market_prob is not None and s.push < 1 and s.win + s.loss > 0]
    if len(graded) >= MIN_CALIBRATION_SIDES:
        for w in WEIGHTS:
            ll = 0.0
            for s in graded:
                q = s.blended(w)
                y = s.win / (s.win + s.loss)
                ll -= y * math.log(q) + (1 - y) * math.log(1 - q)
            st.weight_log_loss.append((w, ll / len(graded)))
        allowed = [t for t in st.weight_log_loss if WEIGHT_RANGE[0] <= t[0] <= WEIGHT_RANGE[1]]
        st.weight = min(allowed, key=lambda t: (t[1], -t[0]))[0]
    else:
        st.note = f"Only {len(graded)} graded lines to tune on; keeping the default {DEFAULT_WEIGHT:.0%} market weight."
    st.by_weight = [summarize(f"{w:.0%} market", select(tune, w, 0.02)) for w in WEIGHTS]

    # 2. Edge threshold, on the tuning games: the one that made the most beyond luck,
    # else the strictest one with a sample, never looser than 2%.
    st.by_min_ev = [summarize(f"{m:.0%}+ edge", select(tune, st.weight, m)) for m in MIN_EVS]
    scored = [(r.score, m) for r, m in zip(st.by_min_ev, MIN_EVS) if r.score > 0]
    if scored:
        st.min_ev = max(max(scored)[1], 0.01)
    else:
        sampled = [m for r, m in zip(st.by_min_ev, MIN_EVS) if r.bets >= MIN_BETS]
        st.min_ev = max(max(sampled), 0.02) if sampled else 0.02

    # 3. The honest test: the chosen settings on games they never saw.
    label = f"{st.weight:.0%} market, {st.min_ev:.0%}+ edge"
    st.in_sample = summarize(label + " (tuning games)", select(tune, st.weight, st.min_ev))
    blind_picks = select(blind, st.weight, st.min_ev)
    st.blind = summarize(label + " (blind games)", blind_picks)
    st.best = st.blind
    st.proven = st.blind.bets >= PROVEN_BETS and st.blind.score > 0
    if not st.proven and not st.note:
        st.note = (f"Blind test: {st.blind.bets} bets" + (f", ROI {st.blind.roi:+.1%}" if st.blind.roi is not None else "")
                   + f". Not proven until it beats the close by more than luck over {PROVEN_BETS}+ blind bets.")

    # 4. Where the blind bets came from: bet type and price band.
    names = {"h2h": "moneyline", "spreads": "spreads", "totals": "over/under"}
    by_market: dict[str, list[Side]] = defaultdict(list)
    by_band: dict[str, list[Side]] = defaultdict(list)
    for s in blind_picks:
        by_market[names.get(s.market, s.market)].append(s)
        by_band[band(s.american)].append(s)
    st.by_market = [summarize(k, v) for k, v in sorted(by_market.items())]
    st.by_band = [summarize(k, v) for k, v in sorted(by_band.items())]

    # 5. Calibration: fitted on tuning games, checked on blind games (both sides of
    # every line, so it is balanced; pushes left out).
    def points(group):
        for s in group:
            if s.push >= 1 or s.win + s.loss <= 0:
                continue
            yield s.blended(st.weight), s.win / (s.win + s.loss)
    fit = list(points(tune))
    if len(fit) >= MIN_CALIBRATION_SIDES:
        st.platt = fit_platt([logit(q) for q, _ in fit], [y for _, y in fit])
    buckets: dict[int, list[tuple[float, float]]] = defaultdict(list)
    for q, y in points(blind):
        buckets[min(int(q * 10), 9)].append((q, y))
    st.calibration = [
        (sum(q for q, _ in v) / len(v), sum(y for _, y in v) / len(v), len(v))
        for _, v in sorted(buckets.items()) if len(v) >= 10
    ]

    # 6. Early bets: the same read against the OPENING line, before the market moved.
    if open_sides:
        o_tune, o_blind = split_blind(open_sides)
        st.early_in_sample = summarize("opening lines (tuning games)", select(o_tune, st.weight, st.min_ev))
        early_blind = select(o_blind, st.weight, st.min_ev)
        st.early_blind = summarize("opening lines (blind games)", early_blind)
        close_fair = {(s.event_id, s.market, s.side, s.point): s.market_prob for s in sides}
        clv = [
            close_fair[k] * american_to_decimal(s.american) - 1
            for s in early_blind
            if (k := (s.event_id, s.market, s.side, s.point)) in close_fair and close_fair[k] is not None
        ]
        st.early_clv = sum(clv) / len(clv) if clv else None
    return st


# ------------------------------------------------------------------- spots
def spot_bets(cfg: SportConfig, results: list[GameResult], close_for, open_for, start: datetime,
              end: datetime) -> list[tuple[str, str, Side]]:
    """(spot key, 'open' or 'close', bet) for every dog (+100 to +220) moneyline in a spot.

    No model involved: each spot is judged purely on what betting it would have done.
    """
    from .odds import no_vig
    from .spots import is_dog_price, team_spots

    out: list[tuple[str, str, Side]] = []
    done = sorted((g for g in results if g.completed and start <= g.date < end), key=lambda g: g.date)
    for g in done:
        spots = team_spots(cfg.key, g.home, g.away, g.date, results, g.neutral)
        if not spots["home"] and not spots["away"]:
            continue
        close = {q.side: q.american for q in close_for(g) if q.market == "h2h"}
        opened = {q.side: q.american for q in open_for(g) if q.market == "h2h"}
        fair = no_vig(close)
        winner = "home" if g.home_score > g.away_score else "away" if g.away_score > g.home_score else "draw"
        for side in ("home", "away"):
            for key in spots[side]:
                for at, prices in (("close", close), ("open", opened)):
                    price = prices.get(side)
                    if price is None or not is_dog_price(price):
                        continue
                    won = winner == side
                    out.append((key, at, Side(cfg.key, g.event_id, "h2h", side, price, fair.get(side, 0.5),
                                              fair.get(side), 0.0, float(won), 0.0, float(not won),
                                              date=g.date.isoformat())))
    return out


def wind_bets(cfg: SportConfig, results: list[GameResult], close_for, weather, start: datetime,
              end: datetime) -> list[tuple[str, str, Side]]:
    """('wind_under', 'close', bet): the under at the closing total for every outdoor game
    with 15+ mph wind at kickoff. Wind history comes in one request per stadium."""
    from .ledger import _settle
    from .odds import no_vig
    from .weather import WINDY_MPH, outdoor, wind_at

    if weather is None or cfg.key not in ("nfl", "mlb"):
        return []
    out: list[tuple[str, str, Side]] = []
    done = [g for g in results if g.completed and start <= g.date < end and not g.neutral]
    by_home: dict[tuple[float, float], list[GameResult]] = defaultdict(list)
    for g in done:
        spot_at = outdoor(cfg.key, g.home)
        if spot_at:
            by_home[spot_at].append(g)
    for (lat, lon), games in by_home.items():
        hours = weather.history(lat, lon, min(g.date for g in games), max(g.date for g in games))
        for g in games:
            wind = wind_at(hours, g.date)
            if wind is None or wind < WINDY_MPH:
                continue
            totals = {q.side: q for q in close_for(g) if q.market == "totals"}
            under, over = totals.get("under"), totals.get("over")
            if not under or not over or under.point is None:
                continue
            fair = no_vig({"over": over.american, "under": under.american})
            win, push, loss = _settle(-(g.home_score + g.away_score), under.point)
            out.append(("wind_under", "close", Side(cfg.key, g.event_id, "totals", "under", under.american,
                                                    fair.get("under", 0.5), fair.get("under"), 0.0,
                                                    win, push, loss, date=g.date.isoformat(), point=under.point)))
    return out


def study_spots(bets: list[tuple[str, str, Side]]) -> list[dict]:
    from .spots import SPOTS

    rows = []
    for key, spot in SPOTS.items():
        close = [s for k, at, s in bets if k == key and at == "close"]
        opened = [s for k, at, s in bets if k == key and at == "open"]
        if not close and not opened:
            continue
        c_tune, c_blind = split_blind(close)
        o_tune, o_blind = split_blind(opened)
        fair = {(s.event_id, s.side): s.market_prob for s in close}
        clv = [fair[(s.event_id, s.side)] * american_to_decimal(s.american) - 1
               for s in opened if fair.get((s.event_id, s.side)) is not None]
        # Weight if it holds up: how much more often the spot won than the close said (tuning games).
        excess = None
        if c_tune:
            excess = sum(s.win for s in c_tune) / len(c_tune) - sum(s.market_prob or 0 for s in c_tune) / len(c_tune)
        blind_open, blind_close = summarize("open, blind", o_blind), summarize("close, blind", c_blind)
        proven = any(r.bets >= PROVEN_BETS and r.score > 0 for r in (blind_open, blind_close))
        rows.append({
            "key": key, "label": spot.label,
            "close_tuned": asdict(summarize("close, tuned", c_tune)), "close_blind": asdict(blind_close),
            "open_tuned": asdict(summarize("open, tuned", o_tune)), "open_blind": asdict(blind_open),
            "open_clv": sum(clv) / len(clv) if clv else None,
            "excess_win": excess, "proven": proven,
        })
    return rows


# ------------------------------------------------------------------ the replay
def replay(
    cfg: SportConfig,
    results: list[GameResult],
    closing: Callable[[GameResult], list[Quote]],
    start: datetime,
    end: datetime,
    refit_days: int = 7,
    min_train_games: int = 50,
    opening: Callable[[GameResult], list[Quote]] | None = None,
) -> tuple[list[Side], int, list[Side]]:
    """Price and grade every side of every closing (and opening) line between ``start`` and ``end``.

    Returns (closing sides, games, opening sides)."""
    from .ledger import _settle

    completed = sorted((g for g in results if g.completed), key=lambda g: g.date)
    rest = rest_days(results) if cfg.rest_effects else {}
    cfg = with_shapes(cfg, results, before=start) if cfg.scoring == "normal" else cfg
    engine = Engine(provider=None, settings=Settings(market_weight=0.0, prop_market_weight=0.0, max_odds=10_000))
    sides: list[Side] = []
    open_sides: list[Side] = []
    games = 0
    cursor = start
    while cursor < end:
        nxt = min(cursor + timedelta(days=refit_days), end)
        train = [g for g in completed if cursor - timedelta(days=cfg.lookback_days) <= g.date < cursor]
        test = [g for g in completed if cursor <= g.date < nxt]
        cursor = nxt
        if len(train) < min_train_games or not test:
            continue
        ratings = fit_ratings(
            train, test[0].date, cfg.half_life_days, cfg.ridge, cfg.link, cfg.unknown_percentile,
            rest_effects=cfg.rest_effects, carryover=cfg.carryover,
        )
        for g in test:
            quotes = [q for q in closing(g) if q.market in ("h2h", "spreads", "totals")]
            if not quotes:
                continue
            games += 1
            pred = predict_game(
                cfg, ratings, g.home, g.away, g.neutral,
                home_b2b=cfg.rest_effects and is_b2b(rest.get((g.event_id, g.home))),
                away_b2b=cfg.rest_effects and is_b2b(rest.get((g.event_id, g.away))),
            )
            hs, as_ = g.home_score, g.away_score
            for line_quotes, bucket in ((quotes, sides), (opening(g) if opening else [], open_sides)):
                if not line_quotes:
                    continue
                event = Event(id=g.event_id, sport=cfg.key, commence_time=g.date, home=g.home, away=g.away,
                              quotes=line_quotes, neutral=g.neutral)
                for p in engine._game_picks(cfg, event, pred):
                    if p.market == "h2h":
                        winner = "home" if hs > as_ else "away" if as_ > hs else "draw"
                        out = (1.0, 0.0, 0.0) if winner == p.side else (0.0, 0.0, 1.0)
                    elif p.market == "spreads":
                        out = _settle(hs - as_ if p.side == "home" else as_ - hs, p.point)
                    else:
                        out = _settle(hs + as_, -p.point) if p.side == "over" else _settle(-(hs + as_), p.point)
                    bucket.append(Side(
                        cfg.key, g.event_id, p.market, p.side, p.american, p.model_prob, p.market_prob,
                        p.push_prob, *out, date=g.date.isoformat(), point=p.point,
                    ))
    return sides, games, open_sides


CARRYOVERS = (1.0, 0.6, 0.35)


def choose_carryover(cfg: SportConfig, results: list[GameResult], start: datetime,
                     end: datetime) -> tuple[float, list[tuple]]:
    """How much last season should count: chosen by the model's own log loss on the
    older (tuning) games, then checked on the newer (blind) games."""
    from .backtest import backtest

    mid = start + (end - start) * (1 - BLIND_SHARE)
    rows = []
    for c in CARRYOVERS:
        trial = replace(cfg, carryover=c)
        t, b = backtest(trial, results, start, mid), backtest(trial, results, mid, end)
        rows.append((c, t.log_loss, t.games, b.log_loss, b.games))
    usable = [r for r in rows if r[2] >= 100]
    chosen = min(usable, key=lambda r: (r[1], -r[0]))[0] if usable else 1.0
    return chosen, rows


def apply_studies(sports: list[SportConfig], studies: dict[str, "SportStudy"]) -> list[SportConfig]:
    """Each sport's config with what its study chose (the carryover)."""
    return [replace(c, carryover=studies[c.key].carryover) if c.key in studies else c for c in sports]


def run_studies(
    sports: list[SportConfig],
    results_for: Callable[[SportConfig, datetime, datetime], list[GameResult]],
    history: "ClosingHistory",
    now: datetime,
    days: int = STUDY_DAYS,
    games_folder=None,
    weather=None,
) -> dict[str, SportStudy]:
    """``games_folder``: also write every final score with its lines to monthly CSV files."""
    out: dict[str, SportStudy] = {}
    start = now - timedelta(days=days)
    for i, cfg in enumerate(sports):
        try:
            results = results_for(cfg, start - timedelta(days=cfg.lookback_days), now)
            carry, carry_rows = choose_carryover(cfg, results, start, now)
            cfg = replace(cfg, carryover=carry)
            # Share the download budget fairly so every sport gets studied each run.
            share = (history.budget - history.fetched) // (len(sports) - i)
            history.prefetch(cfg, [g for g in results if start <= g.date < now], limit=share)
            history.save()  # after each sport, so a long run never loses its downloads
            sides, games, open_sides = replay(
                cfg, results, lambda g, c=cfg: history.quotes(c, g), start, now,
                opening=lambda g, c=cfg: history.open_quotes(c, g),
            )
            if games_folder is not None:
                from .archive import write_games

                done = [g for g in results if g.completed and start <= g.date < now]
                write_games(games_folder, cfg.key, done,
                            {g.event_id: history.quotes(cfg, g) for g in done},
                            {g.event_id: history.open_quotes(cfg, g) for g in done})
        except Exception as exc:  # one sport failing should not stop the others
            log.warning("%s study failed: %s", cfg.key, exc)
            st = SportStudy(cfg.key, cfg.name, note=f"Study failed: {exc}")
        else:
            window = f"{start:%b %-d} - {now:%b %-d, %Y}"
            st = study_sides(sides, cfg, games, window, open_sides)
            st.carryover, st.carryover_test = carry, carry_rows
            close_for = lambda g, c=cfg: history.quotes(c, g)  # noqa: E731
            st.spots = study_spots(
                spot_bets(cfg, results, close_for, lambda g, c=cfg: history.open_quotes(c, g), start, now)
                + wind_bets(cfg, results, close_for, weather, start, now)
            )
        out[cfg.key] = st
    return out


def learned_settings(studies: dict[str, SportStudy]) -> dict[str, float]:
    """Market weights the studies chose, as Settings.weight_overrides."""
    return {f"{k}:game": s.weight for k, s in studies.items() if s.best and s.best.bets >= MIN_BETS}


def load(path) -> dict[str, SportStudy]:
    import json
    from pathlib import Path

    try:
        raw = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {}
    out = {}
    for k, v in raw.get("studies", {}).items():
        try:
            out[k] = SportStudy.from_dict(v)
        except TypeError:
            continue
    return out


def save(path, studies: dict[str, SportStudy], now: datetime, props: dict | None = None) -> None:
    import json
    from pathlib import Path

    Path(path).write_text(json.dumps(
        {"ran_at": now.isoformat(), "studies": {k: s.to_dict() for k, s in studies.items()},
         "props": props or {}}, indent=1))


def load_props(path) -> dict:
    import json
    from pathlib import Path

    try:
        return json.loads(Path(path).read_text()).get("props", {})
    except (OSError, ValueError):
        return {}


def _flipped(opened: list[Quote], close: list[Quote]) -> bool:
    """True when the opening line says the other team was a clear favorite.

    A real move that big (a 14-point swing on the spread) is rare; ESPN's opening
    data occasionally has the two teams the wrong way round. Those games are left
    out of the early-line study rather than counted as huge line moves.
    """
    from .odds import implied_prob

    def home_spread(qs):
        return next((q.point for q in qs if q.market == "spreads" and q.side == "home" and q.point is not None), None)

    def home_ml(qs):
        return next((implied_prob(q.american) for q in qs if q.market == "h2h" and q.side == "home"), None)

    o, c = home_spread(opened), home_spread(close)
    if o is not None and c is not None and o * c < 0 and min(abs(o), abs(c)) >= 2.5 and abs(o - c) >= 6:
        return True
    o, c = home_ml(opened), home_ml(close)
    return o is not None and c is not None and (o - 0.5) * (c - 0.5) < 0 and abs(o - c) >= 0.25


class ClosingHistory:
    """Closing lines saved in the repo (data/history/<sport>.json), so each finished
    game is downloaded once and the studies grow every day for free.

    ``budget`` caps new downloads per run (newest games first); the rest are
    picked up on later runs.
    """

    def __init__(self, folder, fetch: Callable[[SportConfig, GameResult], list[Quote] | None], budget: int = 1500,
                 minutes: float | None = None):
        import time
        from pathlib import Path

        self.folder = Path(folder)
        self.fetch = fetch
        self.budget = budget
        self.deadline = time.monotonic() + minutes * 60 if minutes else None  # stop downloading, keep what we have
        self.fetched = 0
        self._data: dict[str, dict[str, list]] = {}
        self._months: dict[str, dict[str, str]] = {}  # sport -> event id -> month file, for games saved this run
        self._dirty: set[tuple[str, str]] = set()

    def _sport(self, key: str) -> dict[str, list]:
        """Every saved game for a sport: the old single file (read-only now) plus one
        file per month (data/history/<sport>/<YYYY-MM>.json), so finished months never
        change and the repository doesn't grow by a whole file every day."""
        import json

        if key not in self._data:
            store: dict[str, list] = {}
            legacy = self.folder / f"{key}.json"
            for path in ([legacy] if legacy.exists() else []) + sorted((self.folder / key).glob("*.json")):
                try:
                    store.update(json.loads(path.read_text()))
                except (OSError, ValueError):
                    continue
            self._data[key] = store
            self._months[key] = {}
        return self._data[key]

    def prefetch(self, cfg: SportConfig, games: list[GameResult], limit: int | None = None) -> None:
        """Download missing closing lines, newest first, within the budget."""
        store = self._sport(cfg.key)
        done = 0

        def missing(g: GameResult) -> bool:
            # New game; or saved before opening lines were kept; or saved with no closing
            # line by an older version that couldn't read MLB's odds (checked once more).
            rows = store.get(g.event_id)
            if rows is None:
                return True
            checked = {r[1] for r in rows if r[0] == "meta"}
            has_close = any(r[3] == STUDY_BOOK for r in rows)
            has_open = any(r[3] == OPEN_BOOK and r[0] != "meta" for r in rows)
            return CHECK_VERSION not in checked and (not has_close or not has_open)

        for g in sorted((g for g in games if g.completed and missing(g)), key=lambda g: g.date, reverse=True):
            if self.fetched >= self.budget or (limit is not None and done >= limit) or self._late():
                return
            self.fetched += 1
            done += 1
            quotes = self.fetch(cfg, g)
            if quotes is None:
                continue
            rows = [[q.market, q.side, q.point, q.book, q.american] for q in quotes]
            old = store.get(g.event_id) or []
            if not any(r[3] == STUDY_BOOK for r in rows):
                rows += [r for r in old if r[3] == STUDY_BOOK]  # never lose a close we already had
            rows.append(["meta", CHECK_VERSION, None, OPEN_BOOK, 0])  # don't ask again
            store[g.event_id] = rows
            month = f"{g.date:%Y-%m}"
            self._months[cfg.key][g.event_id] = month
            self._dirty.add((cfg.key, month))

    def _late(self) -> bool:
        import time

        return self.deadline is not None and time.monotonic() > self.deadline

    def quotes(self, cfg: SportConfig, game: GameResult) -> list[Quote]:
        rows = self._sport(cfg.key).get(game.event_id) or []
        # Pregame closing lines only (older saves also kept bet365's final in-game prices).
        return [Quote(m, side, point, book, price) for m, side, point, book, price in rows if book == STUDY_BOOK]

    def open_quotes(self, cfg: SportConfig, game: GameResult) -> list[Quote]:
        """Opening DraftKings lines (moneyline and spread), where ESPN kept them.

        A 3-way (soccer) moneyline with no opening draw price can't be devigged, so
        it is left out rather than priced as if it had two sides."""
        rows = self._sport(cfg.key).get(game.event_id) or []
        quotes = [Quote(m, side, point, STUDY_BOOK, price) for m, side, point, book, price in rows
                  if book == OPEN_BOOK and m != "meta"]
        if cfg.draws and not any(q.market == "h2h" and q.side == "draw" for q in quotes):
            quotes = [q for q in quotes if q.market != "h2h"]
        if quotes and _flipped(quotes, self.quotes(cfg, game)):
            return []  # the open names the other team as a big favorite: almost always swapped data
        return quotes

    def save(self) -> None:
        import json

        for key, month in self._dirty:
            path = self.folder / key / f"{month}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                current = json.loads(path.read_text())
            except (OSError, ValueError):
                current = {}
            store = self._data[key]
            current.update({eid: store[eid] for eid, m in self._months[key].items() if m == month})
            path.write_text(json.dumps(current, separators=(",", ":")))
        self._dirty.clear()
