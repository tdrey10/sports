"""One engine cycle: scan -> cards -> ledger -> grade -> dashboard data."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta

from .brain import BrainReport
from .edge import Settings
from .engine import Engine, SportScan
from .ledger import Ledger
from .sports import SPORTS, SportConfig
from .tiers import build_cards

log = logging.getLogger(__name__)


@dataclass
class CycleResult:
    scans: list[SportScan] = field(default_factory=list)
    published: int = 0
    graded: int = 0
    brains: dict[str, BrainReport] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    weights: dict[str, float] = field(default_factory=dict)


def refresh_scores(provider, ledger: Ledger, now: datetime) -> int:
    """Live scores + grading only. Uses ESPN only, so it is free to run often."""
    return ledger.refresh(
        lambda sport, start, end: provider.game_results(SPORTS[sport], start, end),
        lambda sport, game: provider.box_score(SPORTS[sport], game),
        now,
    )


def run_cycle(
    provider,
    sports: list[SportConfig],
    ledger: Ledger,
    settings: Settings,
    now: datetime,
    props: bool = False,
    prop_markets: list[str] | None = None,
    hours_ahead: float | None = None,
    learn: bool = True,
    props_sports: set[str] | None = None,
    props_hours: float | None = None,
    weather=None,
    first_seen=None,
) -> CycleResult:
    """``props_sports`` limits player props to those sport keys (None = every sport)."""
    out = CycleResult()
    if learn:
        # Weights the studies chose come in through settings; the engine's own graded
        # picks take over once a sport has enough of them to be the better sample.
        min_picks = 200 if settings.weight_overrides else 60
        settings = replace(settings, weight_overrides={**settings.weight_overrides, **ledger.learn_weights(min_picks)})
    out.weights = settings.weight_overrides
    settings = replace(settings, clv_trust={k: v["factor"] for k, v in ledger.clv_trust(now).items()})
    engine = Engine(provider, settings, now=now, calibrate=True, weather=weather, first_seen=first_seen)
    for cfg in sports:
        try:
            want_props = props and (props_sports is None or cfg.key in props_sports)
            scan = engine.scan(
                cfg, props=want_props, prop_markets=prop_markets,
                hours_ahead=hours_ahead, props_hours=props_hours,
            )
        except Exception as exc:  # one sport failing should not stop the others
            log.warning("%s scan failed: %s", cfg.key, exc, exc_info=log.isEnabledFor(logging.DEBUG))
            out.warnings.append(f"{cfg.name}: {exc}")
            continue
        out.scans.append(scan)
        out.warnings.extend(scan.warnings)
        if scan.brain:
            out.brains[cfg.key] = scan.brain
    _funnel(out.scans, settings)
    held = settings.board_hour is not None and now.astimezone(ledger.tz).hour < settings.board_hour
    cards = [] if held else build_cards(
        out.scans, settings, now, taken_today=ledger.taken_today(now), best_taken=ledger.best_taken(now)
    )
    out.published = len(ledger.publish(cards, now))
    ledger.withdraw(settings, now)
    if not held:
        ledger.post_parlays(now)
    ledger.update_prices(out.scans, now)
    out.graded = refresh_scores(provider, ledger, now)
    return out


def simulate_history(
    provider,
    sports: list[SportConfig],
    ledger: Ledger,
    settings: Settings,
    days: int = 21,
    runs_per_day: int = 2,
    props: bool = True,
) -> dict[str, BrainReport]:
    """Demo only: replay past days on the synthetic provider so the ledger has
    a graded history (picks are made with data available at that time)."""
    end = provider.now
    brains: dict[str, BrainReport] = {}
    step = timedelta(hours=24 / runs_per_day)
    t = end - timedelta(days=days)
    while t < end:
        provider.clock = t
        res = run_cycle(provider, sports, ledger, settings, t, props=props, hours_ahead=36)
        brains.update(res.brains)
        t += step
    provider.clock = end
    return brains


def _funnel(scans, settings) -> None:
    """Log how many sides survive each rule, so an empty board can be explained."""
    from collections import Counter

    from .edge import best_per_event, qualifying
    from .tiers import LEGIT_MIN_WIN, backed, not_legit

    game = [p for s in scans for p in s.picks if not p.is_prop]
    ev_ok = qualifying(game, settings)
    best = best_per_event(game, settings)
    reasons = Counter()
    for p in best:
        if not backed(p, settings):
            reasons["paused by CLV"] += 1
        elif (r := not_legit(p, settings)):
            reasons[r.split(" (")[0][:60]] += 1
        elif p.win_prob < LEGIT_MIN_WIN:
            reasons["under 45% to win"] += 1
        else:
            reasons["passes"] += 1
    top = sorted(game, key=lambda p: p.ev, reverse=True)[:5]
    print(f"funnel: {len(game)} sides, {len(ev_ok)} with {settings.min_ev:.0%}+ edge, {len(best)} best per game: "
          + ", ".join(f"{k} {v}" for k, v in reasons.most_common()))
    for p in top:
        print(f"  top edge: {p.sport} {p.selection} {p.american:+.0f} ev {p.ev:+.1%} win {p.win_prob:.0%} "
              f"market {p.market_prob if p.market_prob is None else round(p.market_prob, 3)} warn {bool(p.warnings)}")
