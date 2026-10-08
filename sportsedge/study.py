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
MIN_CALIBRATION_SIDES = 300
STUDY_DAYS = 180
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
    proven: bool = False  # some edge threshold beat the closing line by more than luck
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
        if d.get("best"):
            d["best"] = Result(**d["best"])
        if d.get("platt"):
            d["platt"] = tuple(d["platt"])
        d["calibration"] = [tuple(c) for c in d.get("calibration", [])]
        d["weight_log_loss"] = [tuple(c) for c in d.get("weight_log_loss", [])]
        return cls(**d)


def study_sides(sides: list[Side], cfg: SportConfig, games: int, window: str) -> SportStudy:
    st = SportStudy(cfg.key, cfg.name, games=games, sides=len(sides), window=window)
    if not sides:
        st.note = "No finished games with closing odds in the study window yet."
        return st

    # 1. Market weight: the blend whose probabilities best predicted what happened
    # on every side of every closing line (log loss, a proper score; far steadier
    # than picking whichever weight had the luckiest ROI).
    graded = [s for s in sides if s.market_prob is not None and s.push < 1 and s.win + s.loss > 0]
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
        st.note = f"Only {len(graded)} graded lines so far; keeping the default {DEFAULT_WEIGHT:.0%} market weight."
    st.by_weight = [summarize(f"{w:.0%} market", select(sides, w, 0.02)) for w in WEIGHTS]

    # 2. Edge threshold, at the chosen weight: the one that made the most beyond luck
    # over a real sample. If none did, the strictest threshold that still has a
    # sample, never looser than 2%; with no sample at all the default stays.
    st.by_min_ev = [summarize(f"{m:.0%}+ edge", select(sides, st.weight, m)) for m in MIN_EVS]
    proven_at = [(r.score, m) for r, m in zip(st.by_min_ev, MIN_EVS) if r.bets >= PROVEN_BETS and r.score > 0]
    st.proven = bool(proven_at)
    if st.proven:
        st.min_ev = max(proven_at)[1]
    else:
        sampled = [m for r, m in zip(st.by_min_ev, MIN_EVS) if r.bets >= MIN_BETS]
        st.min_ev = max(max(sampled), 0.02) if sampled else 0.02
        st.note = st.note or (
            f"Not yet beating the closing line by more than luck over {PROVEN_BETS}+ bets; "
            "using the strictest threshold with a sample (2% at least)."
            if sampled else f"Fewer than {MIN_BETS} bets cleared any threshold at the closing line; keeping the 2% default.")

    picked = select(sides, st.weight, st.min_ev)
    st.best = summarize(f"{st.weight:.0%} market, {st.min_ev:.0%}+ edge", picked)

    # 3. Where it worked: bet type and price band.
    names = {"h2h": "moneyline", "spreads": "spreads", "totals": "over/under"}
    by_market: dict[str, list[Side]] = defaultdict(list)
    by_band: dict[str, list[Side]] = defaultdict(list)
    for s in picked:
        by_market[names.get(s.market, s.market)].append(s)
        by_band[band(s.american)].append(s)
    st.by_market = [summarize(k, v) for k, v in sorted(by_market.items())]
    st.by_band = [summarize(k, v) for k, v in sorted(by_band.items())]

    # 4. Calibration of the blended probability, on every side (both sides of
    # every line, so it is balanced), pushes left out.
    xs, ys = [], []
    buckets: dict[int, list[tuple[float, float]]] = defaultdict(list)
    for s in sides:
        if s.push >= 1:
            continue
        q = s.blended(st.weight)
        y = s.win / (s.win + s.loss) if s.win + s.loss else 0.0
        xs.append(logit(q))
        ys.append(y)
        buckets[min(int(q * 10), 9)].append((q, y))
    if len(xs) >= MIN_CALIBRATION_SIDES:
        st.platt = fit_platt(xs, ys)
    st.calibration = [
        (sum(q for q, _ in v) / len(v), sum(y for _, y in v) / len(v), len(v))
        for _, v in sorted(buckets.items()) if len(v) >= 10
    ]
    return st


# ------------------------------------------------------------------ the replay
def replay(
    cfg: SportConfig,
    results: list[GameResult],
    closing: Callable[[GameResult], list[Quote]],
    start: datetime,
    end: datetime,
    refit_days: int = 7,
    min_train_games: int = 50,
) -> tuple[list[Side], int]:
    """Price and grade every side of every closing line between ``start`` and ``end``."""
    from .ledger import _settle

    completed = sorted((g for g in results if g.completed), key=lambda g: g.date)
    rest = rest_days(results) if cfg.rest_effects else {}
    cfg = with_shapes(cfg, results, before=start) if cfg.scoring == "normal" else cfg
    engine = Engine(provider=None, settings=Settings(market_weight=0.0, prop_market_weight=0.0, max_odds=10_000))
    sides: list[Side] = []
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
            rest_effects=cfg.rest_effects,
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
            event = Event(id=g.event_id, sport=cfg.key, commence_time=g.date, home=g.home, away=g.away,
                          quotes=quotes, neutral=g.neutral)
            hs, as_ = g.home_score, g.away_score
            for p in engine._game_picks(cfg, event, pred):
                if p.market == "h2h":
                    winner = "home" if hs > as_ else "away" if as_ > hs else "draw"
                    out = (1.0, 0.0, 0.0) if winner == p.side else (0.0, 0.0, 1.0)
                elif p.market == "spreads":
                    out = _settle(hs - as_ if p.side == "home" else as_ - hs, p.point)
                else:
                    out = _settle(hs + as_, -p.point) if p.side == "over" else _settle(-(hs + as_), p.point)
                sides.append(Side(
                    cfg.key, g.event_id, p.market, p.side, p.american, p.model_prob, p.market_prob,
                    p.push_prob, *out,
                ))
    return sides, games


def run_studies(
    sports: list[SportConfig],
    results_for: Callable[[SportConfig, datetime, datetime], list[GameResult]],
    history: "ClosingHistory",
    now: datetime,
    days: int = STUDY_DAYS,
) -> dict[str, SportStudy]:
    out: dict[str, SportStudy] = {}
    start = now - timedelta(days=days)
    for i, cfg in enumerate(sports):
        try:
            results = results_for(cfg, start - timedelta(days=cfg.lookback_days), now)
            # Share the download budget fairly so every sport gets studied each run.
            share = (history.budget - history.fetched) // (len(sports) - i)
            history.prefetch(cfg, [g for g in results if start <= g.date < now], limit=share)
            sides, games = replay(cfg, results, lambda g, c=cfg: history.quotes(c, g), start, now)
        except Exception as exc:  # one sport failing should not stop the others
            log.warning("%s study failed: %s", cfg.key, exc)
            st = SportStudy(cfg.key, cfg.name, note=f"Study failed: {exc}")
        else:
            window = f"{start:%b %-d} - {now:%b %-d, %Y}"
            st = study_sides(sides, cfg, games, window)
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


def save(path, studies: dict[str, SportStudy], now: datetime) -> None:
    import json
    from pathlib import Path

    Path(path).write_text(json.dumps(
        {"ran_at": now.isoformat(), "studies": {k: s.to_dict() for k, s in studies.items()}}, indent=1))


class ClosingHistory:
    """Closing lines saved in the repo (data/history/<sport>.json), so each finished
    game is downloaded once and the studies grow every day for free.

    ``budget`` caps new downloads per run (newest games first); the rest are
    picked up on later runs.
    """

    def __init__(self, folder, fetch: Callable[[SportConfig, GameResult], list[Quote] | None], budget: int = 1500):
        from pathlib import Path

        self.folder = Path(folder)
        self.fetch = fetch
        self.budget = budget
        self.fetched = 0
        self._data: dict[str, dict[str, list]] = {}
        self._dirty: set[str] = set()

    def _sport(self, key: str) -> dict[str, list]:
        import json

        if key not in self._data:
            try:
                self._data[key] = json.loads((self.folder / f"{key}.json").read_text())
            except (OSError, ValueError):
                self._data[key] = {}
        return self._data[key]

    def prefetch(self, cfg: SportConfig, games: list[GameResult], limit: int | None = None) -> None:
        """Download missing closing lines, newest first, within the budget."""
        store = self._sport(cfg.key)
        done = 0
        for g in sorted((g for g in games if g.completed and g.event_id not in store), key=lambda g: g.date, reverse=True):
            if self.fetched >= self.budget or (limit is not None and done >= limit):
                return
            self.fetched += 1
            done += 1
            quotes = self.fetch(cfg, g)
            if quotes is None:
                continue
            store[g.event_id] = [[q.market, q.side, q.point, q.book, q.american] for q in quotes]
            self._dirty.add(cfg.key)

    def quotes(self, cfg: SportConfig, game: GameResult) -> list[Quote]:
        rows = self._sport(cfg.key).get(game.event_id) or []
        # Pregame closing lines only (older saves also kept bet365's final in-game prices).
        return [Quote(m, side, point, book, price) for m, side, point, book, price in rows if book == STUDY_BOOK]

    def save(self) -> None:
        import json

        self.folder.mkdir(parents=True, exist_ok=True)
        for key in self._dirty:
            (self.folder / f"{key}.json").write_text(json.dumps(self._data[key], separators=(",", ":")))
        self._dirty.clear()
