"""Command line interface: ``sportsedge scan nba --props``."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import report
from .backtest import backtest
from .edge import Settings, qualifying
from .engine import Engine
from .sports import ALIASES, SPORTS, resolve


def _provider(args):
    if args.demo:
        from .data.sample import SampleProvider

        return SampleProvider(seed=args.seed)
    from .data.espn import EspnClient

    if args.command in ("ratings", "backtest") or getattr(args, "scores_only", False):
        return EspnClient()  # stats only, no odds key needed
    if getattr(args, "odds_source", "espn") == "espn":
        from .data.live import EspnProvider

        return EspnProvider()  # free: ESPN stats + DraftKings lines via ESPN
    from .data.live import LiveProvider
    from .data.oddsapi import OddsApiClient

    books = args.books.split(",") if args.books else None
    return LiveProvider(odds=OddsApiClient(regions=args.regions, bookmakers=books))


def _now(provider) -> datetime:
    return getattr(provider, "now", None) or datetime.now(timezone.utc)


def cmd_scan(args) -> int:
    settings = Settings(
        min_ev=args.min_ev,
        max_ev=args.max_ev,
        market_weight=args.market_weight,
        prop_market_weight=0.65 if args.odds_source == "espn" and args.prop_market_weight == 0.8 else args.prop_market_weight,
        kelly_multiplier=args.kelly,
        max_stake=args.max_stake,
        bankroll=args.bankroll,
        hide_flagged=not args.show_flagged,
    )
    provider = _provider(args)
    engine = Engine(provider, settings, now=_now(provider))
    if args.demo:
        print("DEMO MODE: synthetic teams, players and odds. Nothing here is a real bet.")
    prop_markets = args.prop_markets.split(",") if args.prop_markets else None
    collected = []
    for cfg in resolve(args.sports):
        try:
            scan = engine.scan(cfg, props=args.props, prop_markets=prop_markets, hours_ahead=args.hours)
        except Exception as exc:  # keep scanning the other sports
            logging.getLogger(__name__).debug("scan failed", exc_info=True)
            print(f"\n=== {cfg.name} === failed: {exc}")
            continue
        print(report.render_scan(scan, settings, top=args.top, all_picks=args.all))
        collected.extend(qualifying(scan.picks, settings))
    remaining = getattr(getattr(provider, "odds", None), "requests_remaining", None)
    if remaining is not None:
        print(f"\nThe Odds API requests remaining: {remaining}")
    if args.json:
        report.write_json(args.json, collected)
        print(f"wrote {len(collected)} picks to {args.json}")
    if args.csv:
        report.write_csv(args.csv, collected)
        print(f"wrote {len(collected)} picks to {args.csv}")
    return 0


def cmd_ratings(args) -> int:
    provider = _provider(args)
    now = _now(provider)
    engine = Engine(provider, now=now)
    for cfg in resolve(args.sports):
        results = provider.game_results(cfg, now - timedelta(days=cfg.lookback_days), now)
        print(f"\n=== {cfg.name} power ratings ===")
        try:
            print(report.render_ratings(engine.fit(cfg, results), args.top))
        except ValueError as exc:
            print(f"  {exc}")
    return 0


def cmd_backtest(args) -> int:
    provider = _provider(args)
    now = _now(provider)
    for cfg in resolve(args.sports):
        start = now - timedelta(days=args.days)
        results = provider.game_results(cfg, start - timedelta(days=cfg.lookback_days), now)
        print(report.render_backtest(backtest(cfg, results, start, now, refit_days=args.refit_days)))
    return 0


DEMO_SPORTS = "nfl,nba,mlb,nhl,epl"


def cmd_study(args) -> int:
    """Replay past games against their closing odds and learn how to weigh picks."""
    from pathlib import Path

    from . import study

    provider = _provider(args)
    now = _now(provider)
    data_dir = Path(args.data_dir)
    history = study.ClosingHistory(
        data_dir / ("demo-history" if args.demo else "history"), provider.closing_quotes, budget=args.budget,
        minutes=args.minutes,
    )
    names = args.sports if args.sports != ["all"] or not args.demo else [DEMO_SPORTS]
    games_dir = data_dir / ("demo-games" if args.demo else "games")
    weather = None
    if not args.demo:
        from .weather import WeatherClient

        weather = WeatherClient()
    boxes = None
    if not args.demo and args.box_minutes:
        from .advanced import BoxStore

        boxes = BoxStore(data_dir / "boxscores", minutes=args.box_minutes)
    studies = study.run_studies(resolve(names), provider.game_results, history, now, days=args.days,
                                games_folder=games_dir, weather=weather,
                                odds_folder=data_dir / ("demo-odds" if args.demo else "odds"), boxes=boxes)
    history.save()
    from . import archive

    prefix = "demo-" if args.demo else ""
    graded = archive.grade_prop_lines(data_dir / f"{prefix}props", data_dir / f"{prefix}props_graded",
                                      resolve(names), provider.game_results, provider.box_score, now)
    props = archive.prop_summary(data_dir / f"{prefix}props_graded")
    print(f"graded {graded} logged prop lines")
    out = data_dir / ("demo-studies.json" if args.demo else "studies.json")
    study.save(out, studies, now, props)
    rows = []
    def rec(r):
        return f"{r.won:.0f}-{r.lost:.0f} ({r.bets})" if r and r.bets else "-"

    def roi(r):
        return f"{r.roi:+.1%}" if r and r.roi is not None else "-"

    for s in studies.values():
        rows.append([
            s.name, s.games, f"{s.weight:.0%}", f"{s.min_ev:.0%}",
            rec(s.in_sample), roi(s.in_sample), rec(s.blind), roi(s.blind),
            roi(s.early_blind), f"{s.early_clv:+.1%}" if s.early_clv is not None else "-",
            "PROVEN" if s.proven else "not yet",
        ])
    print(report.table(
        ["Sport", "Games", "Mkt wt", "Min edge", "Tuned W-L", "Tuned ROI", "Blind W-L", "Blind ROI",
         "Early blind ROI", "Early CLV", "Edge"], rows, "lrrrrrrrrrl"))
    print("Tuned = the games the settings were chosen on (in-sample). Blind = newer games they never saw.")
    print(f"downloaded closing lines for {history.fetched} games; wrote {out}")
    return 0


def _settings(args) -> Settings:
    prop_weight = args.prop_market_weight
    if getattr(args, "odds_source", None) == "espn" and prop_weight == 0.8:
        # ESPN shows the prop line but not its price, so the "market" there is only the line's
        # 50/50 midpoint. Trust our projection a bit more than with real prices.
        prop_weight = 0.65
    return Settings(
        min_ev=args.min_ev,
        max_ev=args.max_ev,
        market_weight=args.market_weight,
        prop_market_weight=prop_weight,
        kelly_multiplier=args.kelly,
        max_stake=args.max_stake,
        hide_flagged=not args.show_flagged,
        engine="legacy" if getattr(args, "demo", False) else "d503",  # demo teams have no history
    )


def _in_season(provider, sports):
    odds = getattr(provider, "odds", None)
    if odds is None:
        return sports
    try:
        active = odds.active_keys()
    except Exception as exc:
        print(f"could not check which sports are in season ({exc}); scanning all requested")
        return sports
    keep = [c for c in sports if c.odds_key in active]
    skipped = [c.name for c in sports if c.odds_key not in active]
    if skipped:
        print("out of season, skipped: " + ", ".join(skipped))
    return keep


class Workspace:
    """Ledger + brain cache + site output for the run/serve commands."""

    def __init__(self, args):
        from pathlib import Path

        from .ledger import Ledger

        self.args = args
        self.demo = args.demo
        data_dir = Path(args.data_dir)
        self.data_dir = data_dir
        self.ledger_path = data_dir / ("demo-ledger.json" if self.demo else "ledger.json")
        self.brain_path = data_dir / ("demo-brain.json" if self.demo else "brain.json")
        self.site_dir = Path(args.site)
        self.ledger = Ledger.load(self.ledger_path, tz=args.tz, start_bankroll=args.start_bankroll)
        self.brains, self.weights = self._load_brain()
        self.provider = _provider(args)
        names = args.sports if args.sports != ["all"] or not self.demo else [DEMO_SPORTS]
        self.sports = resolve(names)
        self.settings = _settings(args)  # no board hold: a pick posts the moment an edge is found
        self.studies = self._load_studies(data_dir)
        if self.studies:
            from .study import apply_studies

            self.sports = apply_studies(self.sports, self.studies)
            from dataclasses import replace

            from .study import learned_settings

            self.settings = replace(
                self.settings,
                weight_overrides={**learned_settings(self.studies), **self.settings.weight_overrides},
                min_ev_overrides={k: s.min_ev for k, s in self.studies.items() if s.best and s.best.bets >= 30},
                calibration={k: s.platt for k, s in self.studies.items() if s.platt},
                # Only spots proven blind add weight: how much more often they won than the close said.
                proven={
                    f"{k}:{r.label}" for k, s in self.studies.items() for r in s.by_market
                    if r.bets >= 60 and r.score > 0
                },
                track={
                    f"{k}:{r.label}": {"bets": r.bets, "won": r.won, "lost": r.lost, "roi": r.roi, "se": r.se}
                    for k, s in self.studies.items() for r in s.by_market + s.by_band
                } | {
                    f"{k}:all": {"bets": s.blind.bets, "won": s.blind.won, "lost": s.blind.lost,
                                 "roi": s.blind.roi, "se": s.blind.se}
                    for k, s in self.studies.items() if s.blind
                },
                spot_weights={
                    f"{k}:{r['key']}": r["excess_win"]
                    for k, s in self.studies.items() for r in s.spots
                    if r.get("proven") and r.get("excess_win")
                },
            )
        if not self.demo:
            from dataclasses import replace

            from .tiers import model_trust

            self.settings = replace(self.settings, model_trust=model_trust(self._forecast_study()))
            self.settings = self._with_nfl_wind(self.settings, data_dir)
            self.settings = self._with_history_spots(self.settings, data_dir)

    def _nfl_history(self):
        """The 20-season NFL test (teaser legs, windy unders), rerun every morning."""
        try:
            return json.loads((self.data_dir / "nfl_history.json").read_text())
        except (OSError, ValueError):
            return None

    def _tennis_schedule(self):
        """Player pairs on ESPN's current tennis schedule (draws change late: lucky losers, withdrawals)."""
        from . import tennis

        src = self.data_dir / "_src" / "tennis"
        try:
            tennis.fetch(src)
        except Exception as exc:
            print(f"could not fetch the ESPN tennis schedule: {exc}")
        try:
            self._tennis_rows = tennis.read_matches(src / "matches.csv")
        except OSError:
            self._tennis_rows = None
            return None
        return tennis.scheduled_pairs(self._tennis_rows)

    def _forecast_study(self):
        """The blind prediction study (how far to move the Vegas line toward our ratings), rerun every morning."""
        try:
            return json.loads((self.data_dir / "forecast_study.json").read_text())
        except (OSError, ValueError):
            return None

    @staticmethod
    def _with_nfl_wind(settings, data_dir):
        """The NFL windy under is proven on 20 seasons of closing lines (nfl_history.json), far more
        than one live season, so that proof sets its weight unless the live study already has one."""
        import json
        from dataclasses import replace

        from .nflhist import wind_under_weight

        key = "nfl:wind_under"
        try:
            res = json.loads((data_dir / "nfl_history.json").read_text())
        except (OSError, ValueError):
            return settings
        weight = wind_under_weight(res)
        if weight <= 0 or key in settings.spot_weights:
            return settings
        return replace(settings, spot_weights={**settings.spot_weights, key: weight})

    @staticmethod
    def _with_history_spots(settings, data_dir):
        """Spots STRONG on 2018+ closing lines in six sports (line_studies.json: blind, tuning and
        beyond the luck control) set their weight unless one is already set."""
        import json
        from dataclasses import replace

        from .linestudies import spot_weights

        try:
            res = json.loads((data_dir / "line_studies.json").read_text())
        except (OSError, ValueError):
            return settings
        new = {k: w for k, w in spot_weights(res).items() if k not in settings.spot_weights}
        return replace(settings, spot_weights={**settings.spot_weights, **new}) if new else settings

    def _first_seen(self, sports):
        """The first moneyline we logged for every upcoming game, to spot early money on a dog."""
        from .archive import moneyline_history

        folder = self.data_dir / ("demo-odds" if self.demo else "odds")
        out = {}
        for cfg in sports:
            hist = moneyline_history(folder, cfg.key)
            out[cfg.key] = {ev: {side: seen[0][1] for side, seen in sides.items() if seen} for ev, sides in hist.items()}
        return out

    def _weather(self):
        from .weather import WeatherClient

        return WeatherClient()

    def _load_studies(self, data_dir):
        from .study import load

        return load(data_dir / ("demo-studies.json" if self.demo else "studies.json"))

    def _load_brain(self):
        import json

        from .brain import BrainReport

        try:
            raw = json.loads(self.brain_path.read_text())
            return {k: BrainReport(**v) for k, v in raw["brains"].items()}, raw.get("weights", {})
        except (OSError, ValueError, KeyError, TypeError):
            return {}, {}

    def now(self):
        return _now(self.provider)

    def cycle(self):
        from .runner import run_cycle, simulate_history

        now = self.now()
        if self.demo and not self.ledger.entries and self.args.history_days:
            print(f"building {self.args.history_days} days of demo history (one-time)...")
            self.brains.update(simulate_history(
                self.provider, self.sports, self.ledger, self.settings,
                days=self.args.history_days, runs_per_day=1, props=self.args.props,
            ))
        sports = self.sports if self.demo else _in_season(self.provider, self.sports)
        if not self.demo and not self._credits_ok(sports):
            self.scores()
            return
        props_sports = set(self.args.props_sports.split(",")) if self.args.props_sports else None
        prop_markets = self.args.prop_markets.split(",") if self.args.prop_markets else None
        res = run_cycle(
            self.provider, sports, self.ledger, self.settings, now,
            props=self.args.props, hours_ahead=self.args.hours,
            props_sports=props_sports, props_hours=self.args.props_hours, prop_markets=prop_markets,
            weather=None if self.demo else self._weather(),
            first_seen=self._first_seen(sports),
            teaser_study=None if self.demo else self._nfl_history(),
            forecast_study=None if self.demo else self._forecast_study(),
            d5_models_path=str(self.data_dir / "d5_models.json"), d5_root=str(self.data_dir),
            injury_dir=str(self.data_dir / f"{'demo-' if self.demo else ''}injuries"),
        )
        self.brains.update(res.brains)
        self.weights = res.weights
        from .insights import backfill

        filled = backfill(self.ledger.entries.values(), res.scans)
        if filled:
            print(f"added game facts to {filled} open picks")
        for w in dict.fromkeys(res.warnings):
            print("warning:", w)
        print(f"published {res.published} new picks, graded {res.graded}")
        if not self.demo:
            _nba_report(self.data_dir, res.scans, now)
        if not self.demo and os.environ.get("ODDS_API_KEY"):
            from . import sharp

            if sharp.due(now) or sharp.is_core(now) or getattr(self.args, "sharp_now", False):
                trust = {k: v["factor"] for k, v in self.ledger.clv_trust(now).items()}
                from .insights import add_facts

                value = sharp.run(resolve([",".join(sharp.SPORTS)]), self.provider, now, clv_trust=trust,
                                  force=getattr(self.args, "sharp_now", False))
                for card in value:
                    add_facts(card, res.scans)  # records, streaks, form, starters, injuries
                added = self.ledger.publish(value, now)
                print(f"sharp value: posted {len(added)}")
                tennis_added = self.ledger.publish(sharp.run_tennis(now, force=getattr(self.args, "sharp_now", False),
                                                                    clv_trust=trust,
                                                                    proven=self.ledger.tennis_proven(),
                                                                    scheduled=self._tennis_schedule(),
                                                                    rows=self._tennis_rows), now)
                print(f"tennis sharp value: posted {len(tennis_added)}")
        from . import archive

        prefix = "demo-" if self.demo else ""
        snaps = archive.log_snapshots(self.data_dir / f"{prefix}odds", res.scans, now)
        props = archive.log_prop_lines(self.data_dir / f"{prefix}props", res.scans, now)
        print(f"logged {snaps} game-line snapshots and {props} prop lines")
        print(f"logged {res.injury_changes} injury changes")
        remaining = getattr(getattr(self.provider, "odds", None), "requests_remaining", None)
        if remaining is not None:
            print(f"The Odds API credits remaining: {remaining}")
        self.save(now)

    def _credits_ok(self, sports) -> bool:
        """Skip the odds scan when it would take credits below the floor."""
        remaining = getattr(getattr(self.provider, "odds", None), "requests_remaining", None)
        try:
            remaining = float(remaining)
        except (TypeError, ValueError):
            return True  # unknown: go ahead
        cost = 3 * len(sports)  # h2h + spreads + totals per sport
        if remaining - cost < self.args.credit_floor:
            print(f"::warning::Only {remaining:.0f} Odds API credits left (scan needs ~{cost}, floor "
                  f"{self.args.credit_floor:.0f}). Skipping odds; updating scores only.")
            return False
        print(f"Odds API credits before scan: {remaining:.0f}")
        return True

    def scores(self):
        from .runner import refresh_scores

        now = self.now()
        graded = refresh_scores(self.provider, self.ledger, now)
        if graded:
            print(f"graded {graded} picks")
        self.save(now)

    def save(self, now):
        import json
        from dataclasses import asdict

        from .site import build_data, write_site

        self.ledger.save(self.ledger_path)
        rivalry = None
        if not self.demo:  # Owner, Oct 29: his picks vs our leans, graded the same way
            from . import rivalry as rv

            try:
                rv.refresh(self.provider, self.data_dir, now)
                rivalry = rv.scoreboard(self.ledger, self.data_dir)
            except Exception as exc:
                print(f"your-picks scoreboard failed: {exc}")
        self.brain_path.write_text(json.dumps(
            {"brains": {k: asdict(v) for k, v in self.brains.items()}, "weights": self.weights}, indent=1))
        from .study import load_props

        data = build_data(self.ledger, self.brains, self.weights, self.sports, now, demo=self.demo,
                          studies=self.studies, ufc=_ufc(self.data_dir, self.demo),
                          props=load_props(self.data_dir / ("demo-studies.json" if self.demo else "studies.json")),
                          nba=None if self.demo else _load_json(self.data_dir / "nba_report.json"),
                          reports={} if self.demo else _reports(self.data_dir), rivalry=rivalry)
        path = write_site(self.site_dir, data)
        o = data["stats"]["overall"]
        print(f"dashboard: {path}  (record {o['won']}-{o['lost']}, {o['units']:+.1f}u, bankroll ${data['stats']['bankroll']:,.2f})")


def cmd_run(args) -> int:
    ws = Workspace(args)
    if args.scores_only:
        ws.scores()
    else:
        ws.cycle()
    return 0


def cmd_serve(args) -> int:
    import functools
    import threading
    import time
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

    ws = Workspace(args)
    ws.cycle()
    handler = functools.partial(SimpleHTTPRequestHandler, directory=str(ws.site_dir))
    handler.log_message = lambda *a, **k: None
    server = ThreadingHTTPServer((args.host, args.port), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"serving http://{args.host}:{args.port}  (scores every {args.scores_every} min, odds every {args.odds_every} min; Ctrl+C to stop)")
    last_odds = time.monotonic()
    try:
        while True:
            time.sleep(args.scores_every * 60)
            try:
                if not ws.demo and time.monotonic() - last_odds >= args.odds_every * 60:
                    ws.cycle()
                    last_odds = time.monotonic()
                else:
                    ws.scores()
            except Exception as exc:  # keep serving through a bad refresh
                print("refresh failed:", exc)
    except KeyboardInterrupt:
        server.shutdown()
    return 0


def cmd_live(args) -> int:
    """Poll in-play games, publish live edges, and (optionally) push them to a GitHub issue."""
    import time
    from datetime import datetime, timedelta, timezone

    from .live import LiveScanner, live_cards
    from .notify import GitHubIssueNotifier, format_live_card

    import os

    if os.environ.get("GITHUB_ACTIONS"):
        args.hours = min(args.hours, 5.0)  # stay inside the job's time limit so the ledger gets saved
    ws = Workspace(args)
    sports = resolve(args.sports)
    names = {c.key: c.name for c in sports}
    scanner = LiveScanner(ws.provider, ws.settings)
    notifier = GitHubIssueNotifier() if args.notify else None
    stop_at = datetime.now(timezone.utc) + timedelta(hours=args.hours)
    last_save = datetime.now(timezone.utc)
    print("live check: " + ", ".join(names.values()) if args.once else
          f"live session: {', '.join(names.values())} for {args.hours:g}h, checking every {args.every:g} min")
    while datetime.now(timezone.utc) < stop_at:
        now = datetime.now(timezone.utc)
        for cfg in sports:
            try:
                picks = scanner.scan(cfg, now)
            except Exception as exc:  # keep the session alive through one bad poll
                print(f"{cfg.name}: live scan failed: {exc}")
                continue
            cards = {(c.pick.event_id, c.tier): c for c in live_cards(picks, scanner, names, ws.ledger.live_taken(), ws.ledger.live_proven())}
            for entry in ws.ledger.publish(list(cards.values()), now):
                card = cards[(entry.event_id, entry.tier)]
                print(f"{entry.tier.upper()} {entry.sport_name}: {entry.selection} {entry.odds:+.0f} "
                      f"({entry.units:g}u, EV {entry.ev:+.1%})")
                if notifier:
                    notifier.post(format_live_card(card))
        remaining = getattr(getattr(ws.provider, "odds", None), "requests_remaining", None)
        if remaining is not None:
            try:
                if float(remaining) < args.min_credits:
                    print(f"stopping: only {remaining} Odds API credits left (floor {args.min_credits})")
                    break
            except ValueError:
                pass
        if args.once:  # one check (scheduled runs), then grade and save
            break
        if now - last_save >= timedelta(minutes=15):
            ws.scores()
            last_save = now
        time.sleep(args.every * 60)
    ws.scores()
    return 0


def _rivalry(ledger, data_dir):
    """The Doctor vs Grim scoreboard from the saved ledgers (no grading, no network)."""
    from . import rivalry

    try:
        return rivalry.scoreboard(ledger, data_dir)
    except Exception as exc:  # the page still builds
        print(f"The Doctor vs Grim scoreboard failed: {exc}")
        return None


def cmd_page(args) -> int:
    """Write the dashboard as an Artifact-ready page from the saved ledger (no network)."""
    import hashlib
    import json
    from datetime import datetime, timezone
    from pathlib import Path

    from .brain import BrainReport
    from .ledger import Ledger
    from .site import build_data, render_html

    data_dir = Path(args.data_dir)
    ledger = Ledger.load(data_dir / "ledger.json", tz=args.tz)
    try:
        raw = json.loads((data_dir / "brain.json").read_text())
        brains = {k: BrainReport(**v) for k, v in raw.get("brains", {}).items()}
        weights = raw.get("weights", {})
    except (OSError, ValueError, TypeError):
        brains, weights = {}, {}
    from .study import load as load_studies, load_props

    from . import rival

    data = build_data(ledger, brains, weights, resolve(args.sports), datetime.now(timezone.utc), ufc=_ufc(data_dir),
                      studies=load_studies(data_dir / "studies.json"), props=load_props(data_dir / "studies.json"),
                      rival=rival.fetch(data_dir / "rival_picks.json"), nba=_load_json(data_dir / "nba_report.json"),
                      reports=_reports(data_dir), rivalry=_rivalry(ledger, data_dir),
                      rival_reported=rival.fetch_reported(data_dir / "rival_dashboard.html"))
    Path(args.out).write_text(render_html(data, standalone=args.standalone))
    if args.app_dir:
        from .site import write_app

        write_app(args.app_dir, data)
    # Same picks and results -> same fingerprint, so a refresh job can skip republishing an unchanged page.
    fingerprint = hashlib.sha1(json.dumps(data["entries"], sort_keys=True, default=str).encode()).hexdigest()[:12]
    print(f"wrote {args.out} ({len(data['entries'])} picks, fingerprint {fingerprint})")
    return 0


def _nba_report(data_dir: Path, scans, now: datetime, budget: int = 40) -> None:
    """Owner, Oct 25: the NBA player report. New box scores, today's injuries and games -> data/nba_report.json."""
    import json

    from . import nbaplayers
    from .data.espn import EspnClient

    client = EspnClient()
    due = _load_json(data_dir / "due_study.json") or {}
    try:
        added = nbaplayers.collect(client, now, data_dir, budget=budget)
        injuries, games = _slate(scans, "nba", now)
        study = _load_json(data_dir / "nba_study.json")
        rep = nbaplayers.build(now, data_dir, injuries, games[:15], study)
        (data_dir / "nba_report.json").write_text(json.dumps(rep, separators=(",", ":")))
        print(f"NBA report: {added} new box scores, {len(rep['players'])} players, {len(rep['matchups'])} games")
    except Exception as exc:  # the report never stops a run
        print(f"NBA report failed: {exc}")
    # Owner, Oct 25: the same for football (NFL and college).
    from . import fbplayers

    for sport in fbplayers.FB_SPORTS:
        try:
            added = fbplayers.collect(client, sport, now, data_dir, budget=budget * 2)
            injuries, games = _slate(scans, sport, now)
            rep = fbplayers.build(sport, now, data_dir, injuries, games[:60], due.get(sport))
            (data_dir / f"{sport}_report.json").write_text(json.dumps(rep, separators=(",", ":")))
            print(f"{sport.upper()} report: {added} new box scores, {len(rep['teams'])} teams, {len(rep['matchups'])} games")
        except Exception as exc:
            print(f"{sport.upper()} report failed: {exc}")
    # And hockey (owner, Oct 25).
    from . import nhlplayers

    try:
        added = nhlplayers.collect(client, now, data_dir, budget=budget)
        injuries, games = _slate(scans, "nhl", now)
        rep = nhlplayers.build(now, data_dir, injuries, games[:20], due.get("nhl"))
        (data_dir / "nhl_report.json").write_text(json.dumps(rep, separators=(",", ":")))
        print(f"NHL report: {added} new box scores, {len(rep['teams'])} teams, {len(rep['matchups'])} games")
    except Exception as exc:
        print(f"NHL report failed: {exc}")
    _game_studies(client, data_dir, now)


def _has_played(rep: dict, team: str, player: str) -> bool:
    """Whether a player shows up in his team's box scores this season (no roster = can't tell: yes)."""
    roster = ((rep.get("teams") or {}).get(team) or {}).get("roster")
    return roster is None or any(r.get("name") == player for r in roster)


def line_text(m: dict) -> str:
    """A game's prices with the team on every number (owner, Oct 25: the Bucs were read as the -155
    favorite when Carolina was; unlabeled home/away columns can't be read backwards if nobody reads
    them)."""
    price = lambda v: "n/a" if v is None else f"{v:+.0f}" if v != 100 else "EVEN"  # noqa: E731
    bits = [f"{m['away']} {price(m.get('away_ml'))}", f"{m['home']} {price(m.get('home_ml'))}"]
    if m.get("spread") is not None:
        bits.append(f"spread: {m['home']} {m['spread']:+g}" if m["spread"] else "spread: pick'em")
    return " · ".join(bits)


def _game_studies(client, data_dir: Path, now: datetime) -> None:
    """Owner, Oct 25: 'a study on every game played in every sport.' Each report's games get every
    situation each team is in and what the factor study (data/factor_study.json) says about it, plus
    who's out sick."""
    import json

    from . import factorstudy
    from .sports import SPORTS

    results = factorstudy.load_results(data_dir / "factor_study.json")
    for sport in ("nba", "nfl", "ncaaf", "nhl"):
        path = data_dir / f"{sport}_report.json"
        rep = _load_json(path)
        if not rep or not rep.get("matchups"):
            continue
        try:
            studies = factorstudy.game_study(sport, rep["matchups"], data_dir, results=results, now=now)
            sick = client.illness(SPORTS[sport])
        except Exception as exc:  # the study never stops a run
            print(f"{sport.upper()} game study failed: {exc}")
            continue
        n = 0
        for m in rep["matchups"]:
            m["line"] = line_text(m)
            rows = studies.get(f"{m['away']} @ {m['home']}")
            if rows is not None:
                m["study"] = rows
                m["study_notes"] = factorstudy.notes(rows)
                n += 1
            # Only players who've played this season (owner, Oct 27: Porzingis hasn't played in a long
            # time; flagging him as news when the line has had it for weeks is misleading).
            ill = [f"🤒 {p} ({t}) is on the report sick" for t in (m["away"], m["home"]) for p in sick.get(t, [])
                   if _has_played(rep, t, p)]
            if ill:
                m["study_notes"] = ill + m.get("study_notes", [])
        path.write_text(json.dumps(rep, separators=(",", ":")))
        print(f"{sport.upper()} game study: {n} of {len(rep['matchups'])} games, "
              f"{sum(len(m.get('study', [])) for m in rep['matchups'])} factors in play")


def _slate(scans, sport: str, now: datetime) -> tuple[dict, list[dict]]:
    """(team -> {"out", "questionable"}, today's and upcoming games with prices) from a sport's scan."""
    scan = next((s for s in scans if s.sport.key == sport), None)
    injuries, games = {}, []
    if scan is None:
        return injuries, games
    prices: dict[str, dict] = {}
    for p in scan.picks:
        if p.market == "h2h" and p.side in ("home", "away"):
            prices.setdefault(p.event_id, {})[p.side] = p.american
        elif p.market == "spreads" and p.side == "home":
            prices.setdefault(p.event_id, {})["spread"] = p.point
    for eid, ctx in scan.contexts.items():
        for team in (ctx.home, ctx.away):
            injuries[team] = {"out": ctx.injured.get(team, []), "questionable": ctx.questionable.get(team, [])}
        if ctx.event.commence_time > now:
            pr = prices.get(eid, {})
            games.append({"home": ctx.home, "away": ctx.away, "start": ctx.event.commence_time.isoformat(),
                          "home_ml": pr.get("home"), "away_ml": pr.get("away"), "spread": pr.get("spread")})
    return injuries, sorted(games, key=lambda g: g["start"])


def _reports(data_dir: Path) -> dict:
    """The football reports the site shows, by sport."""
    return {s: r for s in ("nfl", "ncaaf", "nhl", "tennis") if (r := _load_json(Path(data_dir) / f"{s}_report.json"))}


def _load_json(path: Path) -> dict | None:
    import json

    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None


def cmd_nba_study(args) -> int:
    """Backfill NBA player box scores (this season and last) and run the blind "who's due" study."""
    import json

    from . import nbaplayers, nbastudy
    from .data.espn import EspnClient

    from . import fbplayers

    data_dir = Path(args.data_dir)
    now = datetime.now(timezone.utc)
    client = EspnClient()
    added = nbaplayers.collect(client, now, data_dir, budget=args.budget)
    print(f"NBA players: {added} box scores saved")
    for sport in fbplayers.FB_SPORTS:
        print(f"{sport.upper()} players: {fbplayers.collect(client, sport, now, data_dir, budget=args.budget)} box scores saved")
    from . import nhlplayers

    print(f"NHL players: {nhlplayers.collect(client, now, data_dir, budget=args.budget)} box scores saved")
    # The "who's due" study in every sport with closing lines (owner, Oct 25).
    res = nbastudy.run_all(data_dir / "lines" / "espn")
    (data_dir / "nba_study.json").write_text(json.dumps(res["nba"], indent=1))
    (data_dir / "due_study.json").write_text(json.dumps(res, indent=1))
    for r in res.values():
        print(nbastudy.report(r))
    # Owner, Oct 25: every team and player studied. When the players who matter sit, does the
    # closing line already price it? (NBA and NHL: the sports with enough player box scores.)
    from . import absencestudy

    absent = {}
    for sport in ("nba", "nhl"):
        try:
            absent[sport] = r = absencestudy.run(data_dir, sport)
        except Exception as exc:
            print(f"{sport.upper()} absence study failed: {exc}")
            continue
        print(f"{sport.upper()} absences: {r['games']} games (tune {r['tune_games']}), luck control {r['control']}")
        for row in r["rows"]:
            t, b = row["in_sample"], row["blind"]
            fmt = lambda x: "  -  " if x["flat_roi"] is None else f"{x['flat_roi']:+.1%}"  # noqa: E731
            print(f"  {row['market']:<19} {row['variant']:<58} tune {t['bets']:>4} {fmt(t)}  blind {b['bets']:>4} {fmt(b)} {row['verdict']}")
    (data_dir / "absence_study.json").write_text(json.dumps(absent, indent=1))
    # Owner, Oct 25: every situation that can swing a game, every sport, tested blind.
    from . import factorstudy

    fs = factorstudy.run_all(data_dir / "lines" / "espn")
    for r in fs.values():
        print(factorstudy.report(r))
    (data_dir / "factor_study.json").write_text(json.dumps(fs, separators=(",", ":")))
    # Owner, Oct 28: college hoops efficiency / tempo and NBA pace, against the closing spread and total.
    from . import effstudy

    try:
        eff = effstudy.run_all(data_dir)
        print(effstudy.report(eff))
        (data_dir / "eff_study.json").write_text(json.dumps(eff, separators=(",", ":")))
    except Exception as exc:
        print(f"efficiency study failed: {exc}")
    from . import keystudy

    try:  # Owner, Oct 31: NFL key numbers (3, 7, 10) at the closing spread, tested blind
        ks = keystudy.run(data_dir)
        print(keystudy.report(ks))
        (data_dir / "key_study.json").write_text(json.dumps(ks, indent=1))
    except Exception as exc:
        print(f"key number study failed: {exc}")
    from . import spotstudy

    try:  # Owner, Oct 31: how to bet the proven spots better (timing, stacking, price range)
        ss = spotstudy.run_all(data_dir)
        print(spotstudy.report(ss))
        (data_dir / "spot_study.json").write_text(json.dumps(ss, indent=1))
    except Exception as exc:
        print(f"spot study failed: {exc}")
    from . import propusage

    try:
        pu = propusage.run(data_dir)
        print(propusage.report(pu))
        (data_dir / "prop_usage.json").write_text(json.dumps(pu, indent=1))
    except Exception as exc:
        print(f"usage study failed: {exc}")
    # Owner, Oct 28: when a player is ruled out, do the books move far enough, fast enough?
    from . import injurytiming

    timing = {}
    for sport in injurytiming.SPORTS:
        try:
            timing[sport] = injurytiming.run(sport, data_dir)
        except Exception as exc:
            print(f"{sport.upper()} injury timing failed: {exc}")
            continue
        print(injurytiming.report(timing[sport]))
    (data_dir / "injury_timing.json").write_text(json.dumps(timing, indent=1))
    return 0


def _ufc(data_dir, demo: bool = False) -> dict:
    from .ufc import load

    return {} if demo else load(Path(data_dir))


def cmd_ufc(args) -> int:
    """Refresh UFC fights and DraftKings lines, replay the fighter ratings, save data/ufc.json."""
    from .ufc import run

    out = run(Path(args.data_dir))
    m = out["market"]
    print(f"UFC: {out['fights']} fights replayed, winner picked {(out['accuracy'] or 0):.1%} "
          f"(log loss {(out['log_loss'] or 0):.3f}); {out['lines_saved']} fight lines saved; "
          f"blind test {m['bets']} bets" + (f", ROI {m['roi']:+.1%}" if m['roi'] is not None else ""))
    print(f"{len(out['upcoming'])} upcoming fights")
    return 0


def cmd_data(args) -> int:
    from . import audit

    keys = None if args.sports == ["all"] else args.sports
    print(audit.report(audit.audit(Path(args.data_dir), keys)))
    return 0


def cmd_nfl_history(args) -> int:
    from . import nflhist

    folder = Path(args.data_dir) / "lines" / "nfl"
    if args.fetch:
        print(f"{nflhist.fetch(folder)} NFL games saved from nflverse")
    res = nflhist.run(nflhist.load(folder))
    print(nflhist.report(res))
    out = Path(args.data_dir) / "nfl_history.json"
    out.write_text(json.dumps(res, indent=1, default=str))
    return 0


def cmd_ncaaf_history(args) -> int:
    from . import cfbhist

    res = cfbhist.run(Path(args.data_dir) / "lines" / "ncaaf")
    print(cfbhist.report(res))
    (Path(args.data_dir) / "ncaaf_history.json").write_text(json.dumps(res, indent=1, default=str))
    return 0


def cmd_espn_history(args) -> int:
    from . import espnhist

    data_dir = Path(args.data_dir)
    src = Path(args.src) if args.src else None
    if args.fetch:
        try:
            src = espnhist.fetch(data_dir / "_src" / "espn-archive")
        except Exception as exc:  # keep testing on the saved seasons when the source is unreachable
            print(f"could not fetch {espnhist.REPO}: {exc}")
    out = {}
    for sport in args.sports:
        folder = data_dir / "lines" / "espn" / sport
        if src is not None and (src / sport).exists():
            print(f"{espnhist.build(src / sport, folder, sport)} {sport.upper()} games saved")
        if sport not in espnhist.GRID or not any(folder.glob("*.csv.gz")):
            continue  # NFL and college football have longer model tests of their own; here they feed the studies
        out[sport] = espnhist.run(folder, sport)
        print(espnhist.report(out[sport], sport) + "\n")
    (data_dir / "espn_history.json").write_text(json.dumps(out, indent=1, default=str))
    from . import linestudies

    studies = linestudies.run(data_dir / "lines" / "espn")
    print(linestudies.report(studies))
    (data_dir / "line_studies.json").write_text(json.dumps(studies, indent=1, default=str))
    return 0


def cmd_forecast_study(args) -> int:
    """How much the predictions trust our ratings against Vegas: a blind test on 2018+ games."""
    from . import forecaststudy

    data_dir = Path(args.data_dir)
    res = forecaststudy.run(data_dir / "lines" / "espn", args.sports)
    print(forecaststudy.report(res))
    (data_dir / "forecast_study.json").write_text(json.dumps(res, indent=1, default=str))
    return 0


def cmd_d5_fit(args) -> int:
    """Refit the D503 engine: Elo K / home edge, the own-read model and the market blend per sport."""
    from .d5 import model, qb
    from .data.espn import EspnClient

    data_dir = Path(args.data_dir)
    # Football QB starters from ESPN box scores (the spec's "key" input), this season, newest first.
    client = EspnClient()
    for sport in qb.QB_SPORTS:
        try:
            print(f"D503 {sport}: {qb.collect(sport, client, datetime.now(timezone.utc), data_dir)} new QB box scores")
        except Exception as exc:  # no box scores today: the board reads the QBs it already has
            print(f"D503 {sport}: QB box scores failed ({exc})")
    models = {}
    for sport in ("nfl", "ncaaf", "nba", "mlb", "nhl", "ncaab"):
        try:
            m = models[sport] = model.fit(sport, root=data_dir)
        except Exception as exc:  # a sport with no history keeps its last saved model
            print(f"D503 {sport}: refit failed ({exc})")
            continue
        print(f"D503 {sport}: K {m.k:g} HFA {m.hfa:g}, {m.games} games, accuracy {m.accuracy}, "
              f"blend {[round(x, 3) for x in m.blend]}, bias {m.bias:+.4f}")
    old = model.load_models(data_dir / "d5_models.json")
    model.save({**old, **models}, data_dir / "d5_models.json")
    return 0


def cmd_soccer_study(args) -> int:
    """Soccer spots (form, streaks, head to head, scoring style, draws, rest), blind."""
    from . import soccerstudy

    data_dir = Path(args.data_dir)
    res = soccerstudy.run(data_dir / "games")
    print(soccerstudy.report(res))
    (data_dir / "soccer_study.json").write_text(json.dumps(res, indent=1, default=str))
    return 0


def cmd_my_pick(args) -> int:
    """Owner, Oct 29: record one of his picks for the you-vs-the-leans scoreboard."""
    from . import rivalry

    market, point, side = "h2h", None, None
    if args.spread is not None:
        market, point = "spreads", args.spread
    if args.total:
        market, side, point = "totals", args.total[0].lower(), float(args.total[1])
    if args.cancel:
        print(rivalry.cancel(args.data_dir, args.sport, args.team, datetime.now(timezone.utc), market))
        return 0
    print(rivalry.send(args.data_dir, args.sport, args.team, args.odds, datetime.now(timezone.utc), market, point, side))
    return 0


def cmd_my_parlay(args) -> int:
    """Owner, Oct 29: record one of The Doctor's parlays."""
    from . import rivalry

    legs = [rivalry.parse_leg(x) for x in args.legs]
    print(rivalry.send_parlay(args.data_dir, legs, datetime.now(timezone.utc), grim=args.grim))
    return 0


def cmd_shop_study(args) -> int:
    """Price shopping vs the consensus of other books, NFL and college football, blind."""
    from . import shopstudy

    data_dir = Path(args.data_dir)
    src = data_dir / "_src" / "shop"
    if args.fetch:
        try:
            shopstudy.fetch(src)
        except Exception as exc:  # keep testing on what was saved
            print(f"could not refresh the odds history: {exc}")
    if not (src / "americanfootball_nfl.jsonl.gz").exists():
        print("price-shopping study: no odds history yet")
        return 0
    res = shopstudy.run(src)
    print(shopstudy.report(res))
    (data_dir / "shop_study.json").write_text(json.dumps(res, indent=1, default=str))
    return 0


def cmd_tennis_history(args) -> int:
    from . import tennishist

    data_dir = Path(args.data_dir)
    path = data_dir / "lines" / "tennis" / "hist_odds.csv.gz"
    if args.fetch:
        import requests

        try:
            r = requests.get("https://raw.githubusercontent.com/D503therapper/autonomous-crypto-engine/main/"
                             "data/sports/tennis/hist_odds.csv.gz", timeout=120)
            r.raise_for_status()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(r.content)
        except Exception as exc:  # keep testing on the saved seasons
            print(f"could not refresh tennis odds history: {exc}")
    res = tennishist.run(tennishist.load(path))
    print(tennishist.report(res))
    src = data_dir / "_src" / "tennis"
    if args.fetch:
        from . import tennis

        try:
            tennis.fetch(src)
        except Exception as exc:
            print(f"could not refresh ESPN tennis results: {exc}")
    if (src / "matches.csv").exists():
        from . import tennisform

        res["form"] = tennisform.run(src / "matches.csv", path)
        print(tennisform.report(res["form"]))
    (data_dir / "tennis_history.json").write_text(json.dumps(res, indent=1, default=str))
    return 0


def cmd_tennis(args) -> int:
    """Tennis predictions for every ATP/WTA match with a line, graded on the results."""
    from . import tennis
    from .ledger import Ledger

    data_dir = Path(args.data_dir)
    src = data_dir / "_src" / "tennis"
    try:
        tennis.fetch(src)
    except Exception as exc:
        print(f"could not fetch tennis lines: {exc}")
        if not (src / "odds.json").exists():
            return 0
    try:
        study = json.loads((data_dir / "tennis_history.json").read_text())
    except (OSError, ValueError):
        study = None
    path = data_dir / "ledger.json"
    ledger = Ledger.load(path, tz=args.tz)
    posted, graded = tennis.run(ledger, src, study)
    ledger.save(path)
    print(f"tennis: {posted} match predictions, {graded} graded")
    # Owner, Oct 25: the player report for tennis too (form, surface, fatigue, health, head to head).
    try:
        from . import tennisreport

        rep = tennisreport.build(tennis.read_matches(src / "matches.csv"), datetime.now(timezone.utc))
        (data_dir / "tennis_report.json").write_text(json.dumps(rep, separators=(",", ":")))
        print(f"tennis report: {len(rep['matches'])} matches, {len(rep['hurt'])} health flags")
    except Exception as exc:  # the report never stops a run
        print(f"tennis report failed: {exc}")
    return 0


def cmd_sports(_args) -> int:
    rows = [[k, c.name, c.odds_key, c.espn_path, len(c.props)] for k, c in SPORTS.items()]
    print(report.table(["Key", "Name", "Odds API key", "ESPN path", "Prop markets"], rows, "llllr"))
    print("\nAliases: " + ", ".join(f"{a} -> {','.join(v)}" for a, v in ALIASES.items() if a != "all") + ", all")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="hambino", description="Script Leaks: find +EV bets from team and player stats.")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="command", required=True)

    def common(sp, default_sports="all"):
        sp.add_argument("sports", nargs="*", default=[default_sports], help="sport keys or aliases (default: all)")
        sp.add_argument("--demo", action="store_true", help="use synthetic data (no network / API key)")
        sp.add_argument("--seed", type=int, default=7, help="demo data seed")

    s = sub.add_parser("scan", help="price today's markets and list the best edges")
    common(s)
    s.add_argument("--props", action="store_true", help="also evaluate player props (uses more API credits)")
    s.add_argument("--prop-markets", help="comma separated prop market keys (default: all for the sport)")
    s.add_argument("--min-ev", type=float, default=0.02, help="minimum EV per unit staked (default 0.02)")
    s.add_argument("--max-ev", type=float, default=0.20, help="hide picks above this EV as suspect (default 0.20)")
    s.add_argument("--market-weight", type=float, default=0.75,
                   help="0..1 weight on the no-vig market probability vs the model for game lines (default 0.75)")
    s.add_argument("--prop-market-weight", type=float, default=0.8, help="same for player props (default 0.8)")
    s.add_argument("--kelly", type=float, default=0.25, help="Kelly multiplier (default 0.25 = quarter Kelly)")
    s.add_argument("--max-stake", type=float, default=0.03, help="max bankroll fraction per bet (default 0.03)")
    s.add_argument("--bankroll", type=float, help="show stakes in dollars")
    s.add_argument("--show-flagged", action="store_true",
                   help="include picks where the model and market disagree by more than 12 points")
    s.add_argument("--hours", type=float, help="only events starting within this many hours")
    s.add_argument("--books", help="comma separated bookmaker keys, e.g. draftkings,fanduel")
    s.add_argument("--regions", default="us", help="The Odds API regions (default us)")
    s.add_argument("--odds-source", choices=["espn", "oddsapi"], default="espn",
                   help="espn (free, DraftKings lines) or oddsapi (many books, needs ODDS_API_KEY)")
    s.add_argument("--top", type=int, default=15, help="number of props to show")
    s.add_argument("--all", action="store_true", help="show every qualifying game-line edge, not just one per game")
    s.add_argument("--json", help="write qualifying picks to a JSON file")
    s.add_argument("--csv", help="write qualifying picks to a CSV file")
    s.set_defaults(func=cmd_scan)

    r = sub.add_parser("ratings", help="show fitted team power ratings")
    common(r)
    r.add_argument("--top", type=int, default=25)
    r.set_defaults(func=cmd_ratings)

    b = sub.add_parser("backtest", help="walk-forward accuracy of the game model")
    common(b)
    b.add_argument("--days", type=int, default=60, help="days of games to test on")
    b.add_argument("--refit-days", type=int, default=7)
    b.set_defaults(func=cmd_backtest)

    st = sub.add_parser("study", help="test settings on past games at their closing odds and learn pick weights")
    common(st)
    st.add_argument("--days", type=int, default=730, help="days of finished games to study (about two seasons)")
    st.add_argument("--budget", type=int, default=5000, help="most new games to download closing odds for per run")
    st.add_argument("--minutes", type=float, default=None, help="stop downloading after this many minutes")
    st.add_argument("--box-minutes", type=float, default=0,
                    help="minutes to spend downloading box scores for the advanced stats (0 = off)")
    st.add_argument("--data-dir", default="data")
    st.set_defaults(func=cmd_study)

    def board_opts(sp):
        common(sp)
        sp.add_argument("--props", action="store_true", help="also price player props")
        sp.add_argument("--props-sports", help="only price props for these sports, e.g. nfl,nba")
        sp.add_argument("--props-hours", type=float, help="only price props for games starting within this many hours")
        sp.add_argument("--prop-markets", help="comma separated prop market keys (default: all for each sport)")
        sp.add_argument("--min-ev", type=float, default=0.02)
        sp.add_argument("--max-ev", type=float, default=0.20)
        sp.add_argument("--market-weight", type=float, default=0.75)
        sp.add_argument("--prop-market-weight", type=float, default=0.8)
        sp.add_argument("--kelly", type=float, default=0.25)
        sp.add_argument("--max-stake", type=float, default=0.03)
        sp.add_argument("--show-flagged", action="store_true")
        sp.add_argument("--hours", type=float, default=72, help="only events within this many hours (default 72)")
        sp.add_argument("--books", help="comma separated bookmaker keys")
        sp.add_argument("--regions", default="us")
        sp.add_argument("--data-dir", default="data", help="where the pick ledger lives (default ./data)")
        sp.add_argument("--site", default="site", help="where the dashboard is written (default ./site)")
        sp.add_argument("--tz", default="America/Los_Angeles", help="time zone for 'today' (default Pacific)")
        sp.add_argument("--start-bankroll", type=float, default=100_000.0)
        sp.add_argument("--history-days", type=int, default=14, help="demo only: days of simulated history")
        sp.add_argument("--odds-source", choices=["espn", "oddsapi"], default="espn",
                        help="espn (free, DraftKings lines) or oddsapi (many books, needs ODDS_API_KEY)")
        sp.add_argument("--credit-floor", type=float, default=60,
                        help="never let an odds scan take The Odds API credits below this (default 60)")

    rn = sub.add_parser("run", help="one cycle: scan, publish picks, track lines, grade, build the dashboard")
    board_opts(rn)
    rn.add_argument("--sharp-now", action="store_true",
                    help="check sharp value (Pinnacle vs US books) now, not just at its daily times")
    rn.add_argument("--scores-only", action="store_true",
                    help="only update live scores and grade (ESPN only, no odds credits)")
    rn.set_defaults(func=cmd_run)

    sv = sub.add_parser("serve", help="run the engine continuously and serve the dashboard")
    board_opts(sv)
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8000)
    sv.add_argument("--scores-every", type=float, default=2, help="minutes between live score updates")
    sv.add_argument("--odds-every", type=float, default=60, help="minutes between odds scans")
    sv.set_defaults(func=cmd_serve)

    lv = sub.add_parser("live", help="watch in-play games and post live bets with an edge")
    board_opts(lv)
    lv.add_argument("--every", type=float, default=4, help="minutes between checks (default 4)")
    lv.add_argument("--min-credits", type=float, default=60, help="stop when Odds API credits fall below this")
    lv.add_argument("--notify", action="store_true", help="post each live bet to a GitHub issue (phone alerts)")
    lv.add_argument("--once", action="store_true", help="check in-play games once and exit (for scheduled runs)")
    lv.set_defaults(func=cmd_live, hours=3)

    pg = sub.add_parser("page", help="write the dashboard page from the saved ledger")
    pg.add_argument("sports", nargs="*", default=["nfl,ncaaf,nba,mlb,nhl,soccer"])
    pg.add_argument("--data-dir", default="data")
    pg.add_argument("--tz", default="America/Los_Angeles")
    pg.add_argument("--out", default="great-hambino.html")
    pg.add_argument("--app-dir", help="also write the page as its own website (Home Screen icon) into this folder")
    pg.add_argument("--standalone", action="store_true", help="full HTML document (default: Artifact body)")
    pg.set_defaults(func=cmd_page)

    uf = sub.add_parser("ufc", help="UFC: refresh fights and lines, replay fighter ratings (research until proven)")
    uf.add_argument("--data-dir", default="data")
    uf.set_defaults(func=cmd_ufc)

    da = sub.add_parser("data", help="audit the data archive: games, closing lines, snapshots, props, box scores")
    da.add_argument("sports", nargs="*", default=["all"], help="sport keys (default: every archived sport)")
    da.add_argument("--data-dir", default="data")
    da.set_defaults(func=cmd_data)

    nh = sub.add_parser("nfl-history", help="NFL blind test on every season of closing lines since 2006 (nflverse)")
    nh.add_argument("--fetch", action="store_true", help="download the latest nflverse games first")
    nh.add_argument("--data-dir", default="data")
    nh.set_defaults(func=cmd_nfl_history)

    nc = sub.add_parser("ncaaf-history", help="college football blind test on 2006-2022 lines (cfbfastR, free)")
    nc.add_argument("--data-dir", default="data")
    nc.set_defaults(func=cmd_ncaaf_history)

    eh = sub.add_parser("espn-history", help="NBA/NHL/MLB/college hoops blind test + 15 line studies on 2018+ closing lines")
    eh.add_argument("sports", nargs="*", default=["nfl", "ncaaf", "nba", "nhl", "mlb", "ncaab"])
    eh.add_argument("--fetch", action="store_true", help="refresh the seasons from the public source archive first")
    eh.add_argument("--src", help="a local copy of the source's data/sports/games folder")
    eh.add_argument("--data-dir", default="data")
    eh.set_defaults(func=cmd_espn_history)

    fs = sub.add_parser("forecast-study", help="blind test of the predictions: how far to trust our ratings over Vegas")
    fs.add_argument("--data-dir", default="data")
    fs.add_argument("--sports", nargs="+", default=["mlb", "nhl", "nba", "nfl", "ncaaf", "ncaab"])
    fs.set_defaults(func=cmd_forecast_study)
    ns = sub.add_parser("nba-study", help="NBA player box scores (backfill) + the blind 'who's due' study")
    ns.add_argument("--data-dir", default="data")
    ns.add_argument("--budget", type=int, default=1200, help="most box scores to download this run")
    ns.set_defaults(func=cmd_nba_study)

    d5 = sub.add_parser("d5-fit", help="refit the D503 engine (ratings, own read, market blend) per sport")
    d5.add_argument("--data-dir", default="data")
    d5.set_defaults(func=cmd_d5_fit)

    sc = sub.add_parser("soccer-study", help="soccer form/streak/head-to-head/style spots, blind")
    sc.add_argument("--data-dir", default="data")
    sc.set_defaults(func=cmd_soccer_study)

    mp = sub.add_parser("my-pick", help="record one of your picks for the you-vs-the-leans scoreboard")
    mp.add_argument("sport")
    mp.add_argument("team")
    mp.add_argument("odds", type=float)
    mp.add_argument("--spread", type=float, default=None, help="the team's spread, e.g. -3.5")
    mp.add_argument("--total", nargs=2, metavar=("OVER_UNDER", "POINTS"), default=None)
    mp.add_argument("--cancel", action="store_true", help="take this pick back before its game")
    mp.add_argument("--data-dir", default="data")
    mp.set_defaults(func=cmd_my_pick)
    mx = sub.add_parser("my-parlay", help="record one of your parlays: legs like 'nba|Lakers|-120' or 'nfl|Bills|-110|spread -3.5'")
    mx.add_argument("legs", nargs="+")
    mx.add_argument("--grim", action="store_true", help="log it as Grim's parlay (Script Leaks' side of the contest)")
    mx.add_argument("--data-dir", default="data")
    mx.set_defaults(func=cmd_my_parlay)
    ss = sub.add_parser("shop-study", help="price shopping vs consensus, NFL and college football, blind")
    ss.add_argument("--fetch", action="store_true", help="refresh the odds history and results first")
    ss.add_argument("--data-dir", default="data")
    ss.set_defaults(func=cmd_shop_study)

    th = sub.add_parser("tennis-history", help="tennis blind test on 2012+ closing odds (ATP and WTA)")
    th.add_argument("--fetch", action="store_true", help="refresh the odds history from the public archive first")
    th.add_argument("--data-dir", default="data")
    th.set_defaults(func=cmd_tennis_history)

    tn = sub.add_parser("tennis", help="tennis predictions for every match with a line, graded on results")
    tn.add_argument("--data-dir", default="data")
    tn.add_argument("--tz", default="America/Los_Angeles")
    tn.set_defaults(func=cmd_tennis)

    sub.add_parser("sports", help="list supported sports").set_defaults(func=cmd_sports)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING, format="%(levelname)s %(message)s")
    try:
        return args.func(args)
    except (KeyError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
