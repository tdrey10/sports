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
) -> CycleResult:
    out = CycleResult()
    if learn:
        settings = replace(settings, weight_overrides={**ledger.learn_weights(), **settings.weight_overrides})
    out.weights = settings.weight_overrides
    engine = Engine(provider, settings, now=now, calibrate=True)
    for cfg in sports:
        try:
            scan = engine.scan(cfg, props=props, prop_markets=prop_markets, hours_ahead=hours_ahead)
        except Exception as exc:  # one sport failing should not stop the others
            log.warning("%s scan failed: %s", cfg.key, exc, exc_info=log.isEnabledFor(logging.DEBUG))
            out.warnings.append(f"{cfg.name}: {exc}")
            continue
        out.scans.append(scan)
        out.warnings.extend(scan.warnings)
        if scan.brain:
            out.brains[cfg.key] = scan.brain
    cards = build_cards(
        out.scans, settings, now, taken_today=ledger.taken_today(now), best_taken=ledger.best_taken(now)
    )
    out.published = len(ledger.publish(cards, now))
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
