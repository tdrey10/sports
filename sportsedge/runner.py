"""One engine cycle: scan -> cards -> ledger -> grade -> dashboard data."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from pathlib import Path

from .brain import BrainReport
from .edge import Settings
from .engine import Engine, SportScan
from .ledger import Ledger
from .sports import SPORTS, SportConfig
from .tiers import BEST_BETS, build_cards, gate

log = logging.getLogger(__name__)


@dataclass
class CycleResult:
    scans: list[SportScan] = field(default_factory=list)
    published: int = 0
    graded: int = 0
    brains: dict[str, BrainReport] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    weights: dict[str, float] = field(default_factory=dict)
    injury_changes: int = 0


def refresh_scores(provider, ledger: Ledger, now: datetime) -> int:
    """Live scores + grading only. Uses ESPN only, so it is free to run often."""
    from . import forecasts

    results_for = lambda sport, start, end: provider.game_results(SPORTS[sport], start, end)  # noqa: E731
    graded = ledger.refresh(results_for, lambda sport, game: provider.box_score(SPORTS[sport], game), now)
    forecasts.grade(ledger.forecasts, results_for, now)
    from . import teasers

    teasers.grade(ledger.teasers, results_for, now)
    return graded


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
    teaser_study: dict | None = None,
    forecast_study: dict | None = None,
    d5_models: dict | None = None,
    d5_models_path: str = "data/d5_models.json",
    d5_root: str = "data",
    injury_dir: str | None = None,
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
    if injury_dir:
        # Owner, Oct 28: every injury status change, logged with the time we saw it (the injury-timing study),
        # before the proven finder so a proven "just ruled out" spot can post on this same run.
        from . import injurytiming

        try:
            out.injury_changes = injurytiming.log(Path(injury_dir), out.scans, now, root=d5_root)
        except Exception as exc:  # logging never stops the board
            log.warning("injury log failed: %s", exc)
    held = settings.board_hour is not None and now.astimezone(ledger.tz).hour < settings.board_hour
    # Owner, Oct 24: the D503 formula runs the board for its six sports; the old board keeps the rest
    # (soccer, UFC). The D503 Lock / Dog go first, so the old board never posts a second one.
    d503 = settings.engine == "d503"
    d5_cards: list = []
    legacy_scans = out.scans
    if d503:
        from .d5 import board as d5board
        from .d5.model import load_models

        legacy_scans = [s for s in out.scans if s.sport.key not in d5board.D5_SPORTS]
        if not held:
            models = d5_models if d5_models is not None else load_models(d5_models_path)
            d5_cards = d5board.build(out.scans, now, models, taken=set(ledger.taken_today(now)),
                                     posted_events=set(ledger.open_events(now)), root=d5_root)
    # Owner, Oct 25: only bets that passed a blind test get money. Every card without one goes to 0u
    # (a Lock or Dog without one is a lean); the proven spots on today's games carry the units.
    d5_cards = [gate(c) for c in d5_cards]
    proven_cards: list = []
    if not held:
        from . import proven

        try:
            proven_cards = proven.find(out.scans, now, d5_root)
        except Exception as exc:  # the proven finder never stops the board
            log.warning("proven spots failed: %s", exc, exc_info=True)
    _hold_questionable(proven_cards + d5_cards, out.scans, d5_root)
    taken = set(ledger.taken_today(now)) | {c.tier for c in d5_cards if c.tier in ("lock", "dog") and c.units > 0}
    # The Lock and Dog come from proven bets only: the best proven favorite and the best proven dog.
    for tier, is_it in (("lock", lambda c: c.pick.market == "h2h" and c.pick.american < 0),
                        ("dog", lambda c: c.pick.market == "h2h" and c.pick.american > 0)):
        if tier not in taken:
            pick = next((c for c in proven_cards if is_it(c)), None)
            if pick:
                pick.tier = tier
                taken.add(tier)
    taken |= {"lock", "dog"}  # no unproven Lock or Dog from the old board either
    best_taken = ledger.best_taken(now)
    money = sorted((c for c in proven_cards + d5_cards if c.units > 0 and c.tier != "early"),  # early: own section
                   key=lambda c: (c.units, c.pick.ev), reverse=True)
    for c in money[:max(BEST_BETS - best_taken, 0)]:
        best_taken += 1
        c.best_rank = best_taken
    proven_events = {c.pick.event_id for c in proven_cards}
    cards = [] if held else proven_cards + [c for c in d5_cards if not (c.units == 0 and c.pick.event_id in proven_events)] + [
        gate(c) for c in build_cards(
            legacy_scans, settings, now, taken_today=taken, best_taken=best_taken,
            posted_today=ledger.posted_today(now), posted_events=ledger.open_events(now),
        )]
    if not held:
        # Owner, Oct 25: shop every new money moneyline across the US books before it posts.
        from . import shop
        from .ledger import entry_id

        shop.shop(cards, is_new=lambda c: entry_id(c.pick) not in ledger.entries)
    # Owner, Oct 23/25: a Lock or Dog always carries units; one the old board would post at 0u
    # ("Lean Dog", "best live dog... no units") is not posted at all. Leans stay leans.
    cards = [c for c in cards if not (c.tier in ("lock", "dog") and c.units <= 0)]
    out.published = len(ledger.publish(cards, now))
    if d503 and not held:
        _audit(ledger, now, d5_root)
    ledger.withdraw(settings, now)
    if not held:
        ledger.post_parlays(now)
    ledger.update_prices(out.scans, now)
    from . import forecasts

    forecasts.merge(ledger.forecasts, forecasts.build(out.scans, settings, now, forecast_study), now)
    if not held:
        from . import teasers

        ledger.teasers.extend(teasers.build(out.scans, ledger.teasers, teaser_study, now, ledger.day_of(now)))
        keep_after = now - timedelta(days=30)
        ledger.teasers = [t for t in ledger.teasers
                          if t.status in ("pending", "live") or datetime.fromisoformat(t.created_at) >= keep_after]
    out.graded = refresh_scores(provider, ledger, now)
    return out


def _audit(ledger: Ledger, now: datetime, root: str) -> None:
    """Owner, Oct 25: every money pick the D503 engine posts today, caught or not, and why not."""
    from pathlib import Path

    from . import rival
    from .d5 import audit
    from .d5.board import LAST_SIDES
    from .ledger import D503_MARK

    if not LAST_SIDES:  # the board read no D503 games: nothing to compare (and no fetch)
        return
    try:
        picks = rival.fetch(Path(root) / "rival_picks.json")
        today = ledger.day_of(now)
        posted = {(e.event_id, e.side): e.tier for e in ledger.entries.values()
                  if e.units > 0 and e.status != "void" and any(D503_MARK in r for r in e.reasons)}
        rows = audit.run(picks, LAST_SIDES, posted, now, Path(root) / "d5_audit.json", days=(today,))
        if rows:
            print(f"D503 audit: {sum(r['status'] == 'caught' for r in rows)} of {len(rows)} of his money picks today caught")
    except Exception as exc:  # the audit never stops a run
        log.warning("D503 audit failed: %s", exc)


def _hold_questionable(cards, scans, root: str) -> int:
    """Owner, Oct 28: when a sport's study shows that betting into a key player's unresolved questionable
    status loses to the close, a money pick on that side waits at 0u until the status is final."""
    import json

    from . import injurytiming

    try:
        timing = json.loads((Path(root) / "injury_timing.json").read_text())
    except (OSError, ValueError):
        return 0
    ctxs = {s.sport.key: s.contexts for s in scans}
    held = 0
    for c in cards:
        if c.units <= 0:
            continue
        ctx = (ctxs.get(c.sport) or {}).get(c.pick.event_id)
        who = injurytiming.holding(timing.get(c.sport), c, ctx, c.sport, root)
        if who:
            c.units, c.best_rank = 0.0, None
            if c.tier in ("lock", "dog"):
                c.tier = "lean"
            c.reasons.insert(0, f"⏸ On hold: {who} is questionable. Betting before a key player's status is "
                                "final has lost to the closing line in our study; it posts if he's cleared.")
            held += 1
    return held


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
