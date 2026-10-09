"""In-game ("live") pricing.

A live bet is priced from the current score, the time left and the pregame
model: the rest of the game is assumed to go like the pregame projection,
scaled to the time remaining. That is a simple, honest baseline. It does not
know possession, field position, power plays, pitch counts or who is on the
court, and the books' live models do. So live edges get extra scrutiny:
more weight on the market and a tighter "model and market disagree" filter.
"""

from __future__ import annotations

import math
import re
from dataclasses import replace

from .distributions import discrete_normal_pmf, negbin_pmf_list, normalise
from .models.game import GamePrediction
from .models.ratings import GameResult
from .sports import SportConfig

# (regulation periods, minutes per period); MLB and soccer are handled separately.
CLOCKS = {
    "nfl": (4, 15.0), "ncaaf": (4, 15.0), "nba": (4, 12.0), "ncaab": (2, 20.0), "nhl": (3, 20.0),
}
MIN_FRACTION = 0.01


def _minutes(clock: str) -> float | None:
    m = re.match(r"^\s*(\d+):(\d{2})", clock or "")
    if m:
        return int(m.group(1)) + int(m.group(2)) / 60
    m = re.match(r"^\s*(\d+(?:\.\d+)?)\s*$", clock or "")
    return float(m.group(1)) / 60 if m else None  # "45.3" seconds left


def fraction_remaining(cfg: SportConfig, game: GameResult) -> float | None:
    """Share of regulation still to play, or None if the state can't be read."""
    if game.state != "in" or game.period <= 0:
        return None
    if cfg.key in CLOCKS:
        periods, length = CLOCKS[cfg.key]
        left_in_period = _minutes(game.clock)
        if left_in_period is None:
            return None
        if game.period > periods:  # overtime: treat as a sliver of game left
            return MIN_FRACTION
        remaining = (periods - game.period) * length + min(left_in_period, length)
        return max(remaining / (periods * length), MIN_FRACTION)
    if cfg.key == "mlb":
        detail = (game.detail or "").lower()
        done = 2 * (game.period - 1)
        if detail.startswith(("bot", "mid")):
            done += 1
        elif detail.startswith("end"):
            done += 2
        return max((18 - done) / 18, MIN_FRACTION)
    if cfg.draws:  # soccer: clock like "67'" or "45'+2'"
        m = re.match(r"^\s*(\d+)", game.clock or "")
        if not m:
            return None
        return max((90 - int(m.group(1))) / 90, MIN_FRACTION)
    return None


def live_prediction(
    cfg: SportConfig, pregame: GamePrediction, game: GameResult, frac: float
) -> GamePrediction:
    """Final-score distribution from the current score plus the rest of the game."""
    hs, as_ = game.live_home or 0.0, game.live_away or 0.0
    rem_h, rem_a = pregame.exp_home * frac, pregame.exp_away * frac
    if cfg.scoring == "normal":
        sd_scale = math.sqrt(frac)
        margin_rem = discrete_normal_pmf(rem_h - rem_a, max(cfg.margin_sd * sd_scale, 0.5))
        total_rem = discrete_normal_pmf(rem_h + rem_a, max(cfg.total_sd * sd_scale, 0.5), lo=0)
        cur_m, cur_t = round(hs - as_), round(hs + as_)
        margin = {k + cur_m: p for k, p in margin_rem.items()}
        total = {k + cur_t: p for k, p in total_rem.items()}
        p_win = sum(p for k, p in margin.items() if k > 0)
        p_tie = margin.get(0, 0.0)
        p_home = p_win + 0.5 * p_tie
        return GamePrediction(pregame.home, pregame.away, hs + rem_h, as_ + rem_a, margin, total,
                              p_home, 1 - p_home, 0.0)

    n = cfg.max_score
    disp = cfg.dispersion if cfg.dispersion is not None else 1e9
    ph = negbin_pmf_list(rem_h, disp, n)
    pa = negbin_pmf_list(rem_a, disp, n)
    margin: dict[int, float] = {}
    total: dict[int, float] = {}
    cur_m, cur_t = int(hs - as_), int(hs + as_)
    for i in range(n + 1):
        for j in range(n + 1):
            p = ph[i] * pa[j]
            margin[cur_m + i - j] = margin.get(cur_m + i - j, 0.0) + p
            total[cur_t + i + j] = total.get(cur_t + i + j, 0.0) + p
    margin, total = normalise(margin), normalise(total)
    p_win = sum(p for k, p in margin.items() if k > 0)
    p_tie = margin.get(0, 0.0)
    if cfg.draws:
        return GamePrediction(pregame.home, pregame.away, hs + rem_h, as_ + rem_a, margin, total,
                              p_win, 1 - p_win - p_tie, p_tie)
    share = pregame.exp_home / (pregame.exp_home + pregame.exp_away)
    p_home = p_win + p_tie * (0.5 + (share - 0.5) * 0.5)
    return GamePrediction(pregame.home, pregame.away, hs + rem_h, as_ + rem_a, margin, total,
                          p_home, 1 - p_home, 0.0)


def describe_state(game: GameResult) -> str:
    score = f"{game.away} {game.live_away:.0f} @ {game.home} {game.live_home:.0f}"
    return f"{score} · {game.detail}" if game.detail else score


# --------------------------------------------------------------------- scanner
from datetime import datetime, timedelta, timezone  # noqa: E402

from .data.base import Event, NameMatcher, Quote  # noqa: E402
from .edge import Pick, Settings, evaluate, fmt_point  # noqa: E402
from .models.game import predict_game  # noqa: E402
from .models.ratings import TeamRatings, fit_ratings  # noqa: E402

LIVE_SETTINGS = dict(
    market_weight=0.85,  # the books' live models know game state we don't
    disagreement_warning=0.08,  # big gaps live are usually stale scores, not value
    min_ev=0.03,
    max_ev=0.15,
)
MIN_FRACTION_TO_BET = 0.05  # skip the last few minutes: prices move faster than our data
LIVE_MIN_ODDS = -250  # no heavy live favourites: one bad bounce erases many small wins
LIVE_MAX_UNITS = 1.0  # live edges are the least certain; keep stakes small
REFIT_EVERY = timedelta(hours=2)


def live_settings(base: Settings | None = None) -> Settings:
    base = base or Settings()
    return replace(base, prop_market_weight=base.prop_market_weight, **LIVE_SETTINGS)


class LiveScanner:
    """Prices in-play games. Keeps fitted ratings between polls."""

    def __init__(self, provider, settings: Settings | None = None):
        self.provider = provider
        self.settings = live_settings(settings)
        self._ratings: dict[str, tuple[datetime, TeamRatings, list[str]]] = {}
        self.states: dict[str, GameResult] = {}  # event id -> ESPN live state used

    def _ratings_for(self, cfg: SportConfig, now: datetime) -> tuple[TeamRatings, list[str]]:
        cached = self._ratings.get(cfg.key)
        if cached and now - cached[0] < REFIT_EVERY:
            return cached[1], cached[2]
        results = self.provider.game_results(cfg, now - timedelta(days=cfg.lookback_days), now)
        ratings = fit_ratings(
            results, now, cfg.half_life_days, cfg.ridge, cfg.link, cfg.unknown_percentile,
            rest_effects=cfg.rest_effects, carryover=cfg.carryover,
        )
        teams = sorted(set(ratings.off) | {g.home for g in results} | {g.away for g in results})
        self._ratings[cfg.key] = (now, ratings, teams)
        return ratings, teams

    def in_progress(self, cfg: SportConfig) -> list[GameResult]:
        """Games ESPN shows in progress right now (free, no odds credits)."""
        return [g for g in self.provider.live_games(cfg) if g.state == "in"]

    def scan(self, cfg: SportConfig, now: datetime | None = None) -> list[Pick]:
        now = now or datetime.now(timezone.utc)
        live = self.in_progress(cfg)
        if not live:
            return []
        ratings, teams = self._ratings_for(cfg, now)
        matcher = NameMatcher(teams)
        by_teams = {(g.home, g.away): g for g in live}
        picks: list[Pick] = []
        if hasattr(self.provider, "live_events"):
            events = self.provider.live_events(cfg, live)  # ESPN live odds for these games
        else:
            events = [e for e in self.provider.events(cfg) if e.commence_time <= now]
        for event in events:
            home, away = matcher.match(event.home) or event.home, matcher.match(event.away) or event.away
            game = by_teams.get((home, away))
            if game is None:
                continue
            frac = fraction_remaining(cfg, game)
            if frac is None or frac < MIN_FRACTION_TO_BET:
                continue
            self.states[event.id] = game
            pregame = predict_game(cfg, ratings, home, away, game.neutral)
            pred = live_prediction(cfg, pregame, game, frac)
            picks.extend(self._price(cfg, event, pred, game, frac))
        return picks

    def _price(self, cfg: SportConfig, event: Event, pred: GamePrediction, game: GameResult, frac: float) -> list[Pick]:
        quotes = [q for q in event.quotes if q.market in ("h2h", "spreads", "totals")]
        state = describe_state(game)

        def model(q: Quote):
            if q.market == "h2h":
                if q.side == "draw" and not cfg.draws:
                    return None
                return pred.moneyline(q.side)
            if q.point is None:
                return None
            if q.market == "spreads" and q.side in ("home", "away"):
                return pred.spread(q.side, q.point)
            if q.market == "totals" and q.side in ("over", "under"):
                return pred.total(q.side, q.point)
            return None

        def describe(q: Quote) -> tuple[str, str]:
            team = {"home": event.home, "away": event.away, "draw": "Draw"}.get(q.side, q.side.title())
            if q.market == "h2h":
                sel = f"{team} ML (live)" if q.side != "draw" else "Draw (live)"
            elif q.market == "spreads":
                sel = f"{team} {fmt_point(q.point)} (live)"
            else:
                sel = f"{q.side.title()} {fmt_point(q.point, signed=False)} (live)"
            proj = (
                f"{state}; projected final {event.away} {pred.exp_away:.1f} - {event.home} {pred.exp_home:.1f} "
                f"({frac:.0%} of the game left)"
            )
            return sel, proj

        n_sides = (lambda m: 3 if m == "h2h" and cfg.draws else 2)
        return evaluate(event, quotes, model, describe, self.settings, n_sides)


# ------------------------------------------------------------------- publishing
from .edge import best_per_event  # noqa: E402
from .odds import implied_prob  # noqa: E402
from .tiers import Card, units_for  # noqa: E402


LIVE_WIN_MIN_ODDS = -150  # never bet worse than -150, live included
LIVE_WIN_MIN_PROB = 0.60  # and it has to be a bet we expect to cash at least 60% of the time
LIVE_PLUS_MAX_ODDS = 200  # plus money, but a real chance to win: +100 to +200 only
LIVE_PLUS_MIN_PROB = 0.42  # and at least 42% on our numbers (break-even at +200 is 33%)


def _trusted(p: Pick) -> bool:
    # Live prices across books are rarely in sync, so an "edge" that only comes from one book's
    # stale price (our model below the market) is not trusted.
    return p.market_prob is None or p.model_prob >= p.market_prob


def live_cards(
    picks: list[Pick], scanner: LiveScanner, sport_names: dict[str, str], taken: set[tuple[str, str]] = frozenset()
) -> list[Card]:
    """Up to two live bets per check, across every game in progress:

    - ``live_win``: the bet most likely to cash that still has an edge (EV >= 0, at least 60% to
      cash on our numbers, odds -300 or longer);
    - ``live_plus``: the plus-money bet most likely to win (+100 to +200, 42%+ on our numbers,
      with a real edge: EV >= the live minimum).

    ``taken`` holds (event_id, tier) pairs already posted, so each game gets at most one of each.
    """
    # Live bets post on their own checks (model agrees with the market's direction, real edge),
    # not the pregame blind test.
    ok = [p for p in picks if _trusted(p) and p.ev <= scanner.settings.max_ev and not p.warnings]
    win = [
        p for p in ok
        if p.ev > 0 and p.win_prob >= LIVE_WIN_MIN_PROB and p.american >= LIVE_WIN_MIN_ODDS
        and (p.event_id, "live_win") not in taken
    ]
    plus = [p for p in ok if 100 <= p.american <= LIVE_PLUS_MAX_ODDS and p.win_prob >= LIVE_PLUS_MIN_PROB
            and p.ev >= scanner.settings.min_ev and (p.event_id, "live_plus") not in taken]
    chosen: list[tuple[str, Pick]] = []
    if win:
        chosen.append(("live_win", max(win, key=lambda p: (p.win_prob, p.ev))))
    if plus:
        best_plus = max(plus, key=lambda p: (p.win_prob, p.ev))  # most likely winner first
        if not chosen or best_plus is not chosen[0][1]:
            chosen.append(("live_plus", best_plus))

    cards = []
    for tier, p in chosen:
        game = scanner.states.get(p.event_id)
        name = p.selection.replace(" (live)", "")
        if tier == "live_win":
            headline = f"Live: {name} cashes {p.win_prob:.0%} of the time on our numbers; {p.american:+.0f} only needs {implied_prob(p.american):.0%}."
        else:
            headline = f"Live plus money: {name} at {p.american:+.0f} pays like {implied_prob(p.american):.0%}; we make it {p.win_prob:.0%}."
        reasons = [
            f"{p.projection}.",
            f"Model {p.model_prob:.0%} · market (no-vig) {p.market_prob:.0%} · blended {p.win_prob:.0%}."
            if p.market_prob is not None else f"Model {p.model_prob:.0%}.",
            f"Best live price {p.american:+.0f} at {p.book}. Live lines move fast: confirm the score and price before betting.",
        ]
        cards.append(Card(
            tier=tier, units=min(units_for(p, scanner.settings), LIVE_MAX_UNITS), headline=headline, reasons=reasons, sport=p.sport,
            sport_name=sport_names.get(p.sport, p.sport), pick=p,
            home=game.home if game else "", away=game.away if game else "",
        ))
    return cards
