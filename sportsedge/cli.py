"""Command line interface: ``sportsedge scan nba --props``."""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta, timezone

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
        self.ledger_path = data_dir / ("demo-ledger.json" if self.demo else "ledger.json")
        self.brain_path = data_dir / ("demo-brain.json" if self.demo else "brain.json")
        self.site_dir = Path(args.site)
        self.ledger = Ledger.load(self.ledger_path, tz=args.tz, start_bankroll=args.start_bankroll)
        self.brains, self.weights = self._load_brain()
        self.provider = _provider(args)
        names = args.sports if args.sports != ["all"] or not self.demo else [DEMO_SPORTS]
        self.sports = resolve(names)
        self.settings = _settings(args)

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
        )
        self.brains.update(res.brains)
        self.weights = res.weights
        for w in dict.fromkeys(res.warnings):
            print("warning:", w)
        print(f"published {res.published} new picks, graded {res.graded}")
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
        self.brain_path.write_text(json.dumps(
            {"brains": {k: asdict(v) for k, v in self.brains.items()}, "weights": self.weights}, indent=1))
        data = build_data(self.ledger, self.brains, self.weights, self.sports, now, demo=self.demo)
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
    print(f"live session: {', '.join(names.values())} for {args.hours:g}h, checking every {args.every:g} min")
    while datetime.now(timezone.utc) < stop_at:
        now = datetime.now(timezone.utc)
        for cfg in sports:
            try:
                picks = scanner.scan(cfg, now)
            except Exception as exc:  # keep the session alive through one bad poll
                print(f"{cfg.name}: live scan failed: {exc}")
                continue
            cards = {c.pick.event_id: c for c in live_cards(picks, scanner, names)}
            for entry in ws.ledger.publish(list(cards.values()), now):
                card = cards[entry.event_id]
                print(f"LIVE {entry.sport_name}: {entry.selection} {entry.odds:+.0f} ({entry.units:g}u, EV {entry.ev:+.1%})")
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
        if now - last_save >= timedelta(minutes=15):
            ws.scores()
            last_save = now
        time.sleep(args.every * 60)
    ws.scores()
    return 0


def cmd_page(args) -> int:
    """Write the dashboard as an Artifact-ready page from the saved ledger (no network)."""
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
    data = build_data(ledger, brains, weights, resolve(args.sports), datetime.now(timezone.utc))
    Path(args.out).write_text(render_html(data, standalone=args.standalone))
    print(f"wrote {args.out} ({len(data['entries'])} picks)")
    return 0


def cmd_sports(_args) -> int:
    rows = [[k, c.name, c.odds_key, c.espn_path, len(c.props)] for k, c in SPORTS.items()]
    print(report.table(["Key", "Name", "Odds API key", "ESPN path", "Prop markets"], rows, "llllr"))
    print("\nAliases: " + ", ".join(f"{a} -> {','.join(v)}" for a, v in ALIASES.items() if a != "all") + ", all")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="funky", description="Funky Buttlovin: find +EV bets from team and player stats.")
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
    s.add_argument("--max-ev", type=float, default=0.30, help="hide picks above this EV as suspect (default 0.30)")
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

    def board_opts(sp):
        common(sp)
        sp.add_argument("--props", action="store_true", help="also price player props")
        sp.add_argument("--props-sports", help="only price props for these sports, e.g. nfl,nba")
        sp.add_argument("--props-hours", type=float, help="only price props for games starting within this many hours")
        sp.add_argument("--prop-markets", help="comma separated prop market keys (default: all for each sport)")
        sp.add_argument("--min-ev", type=float, default=0.02)
        sp.add_argument("--max-ev", type=float, default=0.30)
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
        sp.add_argument("--start-bankroll", type=float, default=1000.0)
        sp.add_argument("--history-days", type=int, default=14, help="demo only: days of simulated history")
        sp.add_argument("--odds-source", choices=["espn", "oddsapi"], default="espn",
                        help="espn (free, DraftKings lines) or oddsapi (many books, needs ODDS_API_KEY)")
        sp.add_argument("--credit-floor", type=float, default=60,
                        help="never let an odds scan take The Odds API credits below this (default 60)")

    rn = sub.add_parser("run", help="one cycle: scan, publish picks, track lines, grade, build the dashboard")
    board_opts(rn)
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
    lv.set_defaults(func=cmd_live, hours=3)

    pg = sub.add_parser("page", help="write the dashboard page from the saved ledger")
    pg.add_argument("sports", nargs="*", default=["nfl,nba,mlb,nhl,soccer"])
    pg.add_argument("--data-dir", default="data")
    pg.add_argument("--tz", default="America/Los_Angeles")
    pg.add_argument("--out", default="funky-buttlovin.html")
    pg.add_argument("--standalone", action="store_true", help="full HTML document (default: Artifact body)")
    pg.set_defaults(func=cmd_page)

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
