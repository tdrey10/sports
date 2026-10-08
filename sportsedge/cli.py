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

    if args.command in ("ratings", "backtest"):
        return EspnClient()  # stats only, no odds key needed
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


def cmd_sports(_args) -> int:
    rows = [[k, c.name, c.odds_key, c.espn_path, len(c.props)] for k, c in SPORTS.items()]
    print(report.table(["Key", "Name", "Odds API key", "ESPN path", "Prop markets"], rows, "llllr"))
    print("\nAliases: " + ", ".join(f"{a} -> {','.join(v)}" for a, v in ALIASES.items() if a != "all") + ", all")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="sportsedge", description="Find +EV bets from team and player stats.")
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
    s.add_argument("--market-weight", type=float, default=0.5,
                   help="0..1 weight on the no-vig market probability vs the model (default 0.5)")
    s.add_argument("--kelly", type=float, default=0.25, help="Kelly multiplier (default 0.25 = quarter Kelly)")
    s.add_argument("--max-stake", type=float, default=0.03, help="max bankroll fraction per bet (default 0.03)")
    s.add_argument("--bankroll", type=float, help="show stakes in dollars")
    s.add_argument("--show-flagged", action="store_true",
                   help="include picks where the model and market disagree by more than 12 points")
    s.add_argument("--hours", type=float, help="only events starting within this many hours")
    s.add_argument("--books", help="comma separated bookmaker keys, e.g. draftkings,fanduel")
    s.add_argument("--regions", default="us", help="The Odds API regions (default us)")
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
