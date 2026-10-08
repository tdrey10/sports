"""The scan pipeline: data -> ratings -> predictions -> priced edges."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .brain import BrainReport, train
from .data.base import DataProvider, Event, NameMatcher, Quote, is_out
from .edge import Pick, Settings, evaluate, fmt_point
from .models.game import GamePrediction, baseline_team_score, predict_game
from .models.props import PlayerGame, PropProjection, project_prop
from .models.ratings import GameResult, TeamRatings, fit_ratings, is_b2b
from .sports import SportConfig

log = logging.getLogger(__name__)


@dataclass
class EventContext:
    event: Event
    home: str  # stats-source (ESPN) team names
    away: str
    neutral: bool
    home_rest: float | None
    away_rest: float | None
    injuries: dict[str, list[str]] = field(default_factory=dict)  # team -> players out

    @property
    def home_b2b(self) -> bool:
        return is_b2b(self.home_rest)

    @property
    def away_b2b(self) -> bool:
        return is_b2b(self.away_rest)


@dataclass
class SportScan:
    sport: SportConfig
    picks: list[Pick] = field(default_factory=list)
    predictions: dict[str, GamePrediction] = field(default_factory=dict)
    contexts: dict[str, EventContext] = field(default_factory=dict)
    results: list[GameResult] = field(default_factory=list)
    ratings: TeamRatings | None = None
    brain: BrainReport | None = None
    warnings: list[str] = field(default_factory=list)
    events: int = 0


class Engine:
    def __init__(
        self,
        provider: DataProvider,
        settings: Settings | None = None,
        now: datetime | None = None,
        calibrate: bool = False,
    ):
        self.provider = provider
        self.settings = settings or Settings()
        self.now = now or datetime.now(timezone.utc)
        self.calibrate = calibrate

    # ------------------------------------------------------------------ public
    def scan(
        self,
        cfg: SportConfig,
        props: bool = False,
        prop_markets: list[str] | None = None,
        hours_ahead: float | None = None,
    ) -> SportScan:
        scan = SportScan(cfg)
        events = [e for e in self.provider.events(cfg) if e.commence_time > self.now]
        if hours_ahead is not None:
            horizon = self.now + timedelta(hours=hours_ahead)
            events = [e for e in events if e.commence_time <= horizon]
        scan.events = len(events)
        if not events:
            scan.warnings.append(f"{cfg.name}: no upcoming events with odds")
            return scan

        results = self.provider.game_results(
            cfg, self.now - timedelta(days=cfg.lookback_days), self.now + timedelta(days=10)
        )
        scan.results = results
        if self.calibrate:
            cfg, scan.brain = train(cfg, results, self.now)
        injuries = self._injuries(cfg)
        try:
            ratings = self.fit(cfg, results)
        except ValueError as exc:
            scan.warnings.append(f"{cfg.name}: {exc}")
            return scan
        scan.ratings = ratings

        teams = set(ratings.off) | {g.home for g in results} | {g.away for g in results}
        matcher = NameMatcher(teams)
        scheduled = [g for g in results if not g.completed]
        injury_matcher = NameMatcher(injuries) if injuries else None

        for event in events:
            home = matcher.match(event.home) or event.home
            away = matcher.match(event.away) or event.away
            for odds_name, name in ((event.home, home), (event.away, away)):
                if not ratings.knows(name):
                    scan.warnings.append(
                        f"{cfg.name}: no rating for {odds_name!r}; using a "
                        f"{ratings.unknown_percentile:.0f}th-percentile team"
                    )
            neutral = event.neutral or _is_neutral(scheduled, home, away, event.commence_time)
            ctx = EventContext(
                event, home, away, neutral,
                _rest_before(results, home, event.commence_time),
                _rest_before(results, away, event.commence_time),
            )
            if injury_matcher:
                for team in (home, away):
                    key = injury_matcher.match(team)
                    if key:
                        ctx.injuries[team] = [p for p, status in injuries[key] if is_out(status)]
            scan.contexts[event.id] = ctx
            use_rest = cfg.rest_effects
            pred = predict_game(
                cfg, ratings, home, away, neutral,
                home_b2b=use_rest and ctx.home_b2b, away_b2b=use_rest and ctx.away_b2b,
            )
            scan.predictions[event.id] = pred
            scan.picks.extend(self._game_picks(cfg, event, pred))

            if props and cfg.props:
                markets = [m for m in (prop_markets or cfg.props) if m in cfg.props]
                scan.picks.extend(self._prop_picks(cfg, event, pred, ratings, results, markets, scan, ctx))

        scan.picks.sort(key=lambda p: p.ev, reverse=True)
        return scan

    def fit(self, cfg: SportConfig, results: list[GameResult]) -> TeamRatings:
        return fit_ratings(
            results,
            as_of=self.now,
            half_life_days=cfg.half_life_days,
            ridge=cfg.ridge,
            link=cfg.link,
            unknown_percentile=cfg.unknown_percentile,
            rest_effects=cfg.rest_effects,
        )

    def _injuries(self, cfg: SportConfig) -> dict[str, list[tuple[str, str]]]:
        getter = getattr(self.provider, "injuries", None)
        if getter is None:
            return {}
        try:
            return getter(cfg) or {}
        except Exception as exc:  # injury feeds are best effort
            log.warning("injuries for %s unavailable: %s", cfg.key, exc)
            return {}

    # --------------------------------------------------------------- internals
    def _game_picks(self, cfg: SportConfig, event: Event, pred: GamePrediction) -> list[Pick]:
        quotes = [q for q in event.quotes if q.market in ("h2h", "spreads", "totals")]

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
                sel = f"{team} ML" if q.side != "draw" else "Draw"
            elif q.market == "spreads":
                sel = f"{team} {fmt_point(q.point)}"
            else:
                sel = f"{q.side.title()} {fmt_point(q.point, signed=False)}"
            proj = (
                f"{event.away} {pred.exp_away:.1f} - {event.home} {pred.exp_home:.1f} "
                f"(margin {pred.exp_margin:+.1f}, total {pred.exp_total:.1f})"
            )
            return sel, proj

        def n_sides(market: str) -> int:
            return 3 if market == "h2h" and cfg.draws else 2

        return evaluate(event, quotes, model, describe, self.settings, n_sides)

    def _prop_picks(
        self,
        cfg: SportConfig,
        event: Event,
        pred: GamePrediction,
        ratings: TeamRatings,
        results: list[GameResult],
        markets: list[str],
        scan: SportScan,
        ctx: EventContext,
    ) -> list[Pick]:
        quotes = self.provider.prop_quotes(cfg, event, markets)
        if not quotes:
            return []
        logs: dict[str, list[PlayerGame]] = {}
        team_factor: dict[str, float] = {}
        for team, exp in ((pred.home, pred.exp_home), (pred.away, pred.exp_away)):
            for pg in self.provider.player_games(cfg, results, team):
                logs.setdefault(pg.player, []).append(pg)
                team_factor[pg.player] = exp / baseline_team_score(ratings, team)
        if not logs:
            scan.warnings.append(f"{event.away} @ {event.home}: no player box scores for props")
            return []
        players = NameMatcher(logs, threshold=0.88)
        ruled_out = {p for names in ctx.injuries.values() for p in names}
        out_matcher = NameMatcher(ruled_out, threshold=0.9)
        cache: dict[tuple[str, str], PropProjection | None] = {}
        missing: set[str] = set()

        def projection(q: Quote) -> PropProjection | None:
            if ruled_out and out_matcher.match(q.player or ""):
                return None  # listed out: the prop will be voided or pulled
            name = players.match(q.player or "")
            if name is None:
                missing.add(q.player or "?")
                return None
            key = (name, q.market)
            if key not in cache:
                cache[key] = project_prop(cfg.props[q.market], q.market, name, logs[name], team_factor.get(name, 1.0))
            return cache[key]

        def model(q: Quote):
            proj = projection(q)
            if proj is None or q.point is None:
                return None
            return proj.probs(q.side, q.point)

        def describe(q: Quote) -> tuple[str, str]:
            proj = projection(q)
            spec = cfg.props[q.market]
            sel = f"{q.player} {q.side.title()} {fmt_point(q.point, signed=False)} {spec.label}"
            desc = f"projects {proj.mean:.1f} ± {proj.sd:.1f} (last {proj.games} games)" if proj else ""
            return sel, desc

        picks = evaluate(event, quotes, model, describe, self.settings, lambda m: 2)
        if missing:
            log.info("no box scores matched for %d players: %s", len(missing), ", ".join(sorted(missing)[:10]))
        return picks


def _rest_before(results: list[GameResult], team: str, when: datetime) -> float | None:
    prev = [g.date for g in results if team in (g.home, g.away) and g.date < when - timedelta(hours=3)]
    if not prev:
        return None
    return (when - max(prev)).total_seconds() / 86400.0


def _is_neutral(scheduled: list[GameResult], home: str, away: str, when: datetime) -> bool:
    for g in scheduled:
        if g.home == home and g.away == away and abs((g.date - when).total_seconds()) < 36 * 3600:
            return g.neutral
    return False
