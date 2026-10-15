"""Sharp value: regular sportsbooks priced better than Pinnacle's fair line.

Pinnacle takes big bets from professionals, so its no-vig price is about the most
accurate probability there is. When DraftKings, FanDuel, BetMGM or another US book
pays more than that fair price by 2%+ (and -170 or better), the bet is +EV without
relying on our own model. It's the standard method of value-betting software.

Odds come from The Odds API (free plan: 500 requests a month, key in
ODDS_API_KEY). One check of one sport's moneylines across Pinnacle and up to nine US
books costs 1 request, spent only on sports in season with a game in the next 36 hours.
Checks run at about 8 AM, noon, 3 PM and 5 PM Pacific: noon and 5 PM (and Saturday 8 AM)
always, the others only while the month's budget can carry every check to month end. Each pick is matched to its ESPN
game so it is graded and its closing line value tracked like every other pick; the
adaptive layer pauses sharp value in a sport whose picks keep losing to the close.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from types import SimpleNamespace

import requests

from .data.base import NameMatcher
from .edge import Pick
from .odds import american_to_decimal, decimal_to_american, kelly_fraction, no_vig
from .sports import SportConfig
from .tiers import Card

log = logging.getLogger(__name__)

API = "https://api.the-odds-api.com/v4"
SHARP = "pinnacle"
US_BOOKS = ("draftkings", "fanduel", "betmgm", "williamhill_us", "espnbet", "betrivers", "fanatics",
            "ballybet", "hardrockbet")
BOOK_NAMES = {"draftkings": "DraftKings", "fanduel": "FanDuel", "betmgm": "BetMGM", "williamhill_us": "Caesars",
              "espnbet": "ESPN BET", "betrivers": "BetRivers", "fanatics": "Fanatics", "ballybet": "Bally Bet",
              "hardrockbet": "Hard Rock"}
MARKETS = ("h2h",)  # moneylines only: one request per sport keeps the free plan's 500 a month
SPORTS = ("nfl", "nba", "nhl", "mlb", "ncaaf")  # the biggest markets, where Pinnacle's line is sharpest
MIN_EV = 0.02
MAX_EV = 0.15  # bigger than this is almost always a stale or wrong price
MAX_FAVORITE = -170
MAX_UNITS = 1.0  # new method: small until its CLV record is in
CORE_HOURS_UTC = (19, 0)  # about noon and 5 PM Pacific: always checked (credits allowing)
EXTRA_HOURS_UTC = (15, 22)  # about 8 AM and 3 PM Pacific: checked when the month's budget can carry them
SATURDAY_HOUR_UTC = 15  # on Saturdays the 8 AM check is core: the early college football kickoffs
CHECK_HOURS_UTC = CORE_HOURS_UTC + EXTRA_HOURS_UTC
UPCOMING_HOURS = 36  # only spend a request on a sport with a game starting within this window
CREDIT_FLOOR = 40
CREDITS_PER_SPORT = 1  # one market, up to ten books


def due(now: datetime) -> bool:
    """Up to four checks a day (8 AM, noon, 3 PM, 5 PM Pacific): the first run in each check hour."""
    return now.hour in CHECK_HOURS_UTC and now.minute < 35


def is_core(now: datetime) -> bool:
    return now.hour in CORE_HOURS_UTC or (now.weekday() == 5 and now.hour == SATURDAY_HOUR_UTC)


def affordable(remaining: float | None, cost: int, now: datetime, core: bool) -> bool:
    """Core checks run while credits last (above the floor). Extra checks run only when what is
    left would still pay for every check, extras included, through the end of the month, so the
    free plan never runs dry before it resets."""
    if remaining is None:
        return core
    spare = remaining - CREDIT_FLOOR
    if core:
        return spare >= cost
    nxt = datetime(now.year + now.month // 12, now.month % 12 + 1, 1, tzinfo=now.tzinfo)
    days_left = max((nxt.date() - now.date()).days, 1)
    return spare >= cost * days_left * len(CHECK_HOURS_UTC)


class SharpClient:
    def __init__(self, api_key: str | None = None, session: requests.Session | None = None):
        self.api_key = api_key or os.environ.get("ODDS_API_KEY")
        self.session = session or requests.Session()
        self.remaining: float | None = None

    def _track(self, resp) -> None:
        try:
            self.remaining = float(resp.headers.get("x-requests-remaining"))
        except (TypeError, ValueError):
            pass

    def active(self) -> set[str]:
        resp = self.session.get(f"{API}/sports", params={"apiKey": self.api_key}, timeout=20)  # free
        self._track(resp)
        resp.raise_for_status()
        return {s["key"] for s in resp.json() if s.get("active")}

    def odds(self, cfg: SportConfig) -> list[dict]:
        resp = self.session.get(f"{API}/sports/{cfg.odds_key}/odds", params={
            "apiKey": self.api_key, "markets": ",".join(MARKETS), "oddsFormat": "american",
            "bookmakers": ",".join((SHARP,) + US_BOOKS),
        }, timeout=20)
        self._track(resp)
        resp.raise_for_status()
        return resp.json()


def _prices(ev: dict) -> dict[str, dict[tuple, dict[str, float]]]:
    """book -> (market, point key) -> side -> american price."""
    home, away = ev["home_team"], ev["away_team"]
    out: dict[str, dict] = {}
    for b in ev.get("bookmakers", []):
        for m in b.get("markets", []):
            for o in m.get("outcomes", []):
                name = o.get("name")
                side = "home" if name == home else "away" if name == away else "draw" if str(name).lower() == "draw" else None
                if side is None:
                    continue
                point = o.get("point")
                # Both sides of a spread share one line: key it by the home team's point.
                line = None if m["key"] == "h2h" else (point if side == "home" else -point if point is not None else None)
                out.setdefault(b["key"], {}).setdefault((m["key"], line), {})[side] = (float(o["price"]), point)
    return out


def find_value(cfg: SportConfig, ev: dict, now: datetime) -> list[dict]:
    """Every US-book price that beats Pinnacle's no-vig line by MIN_EV or more."""
    prices = _prices(ev)
    sharp = prices.get(SHARP)
    if not sharp:
        return []
    out = []
    for (market, line), sides in sharp.items():
        if market == "h2h" and cfg.draws and "draw" not in sides:
            continue
        fair = no_vig({s: p for s, (p, _) in sides.items()})
        for book, mk in prices.items():
            if book == SHARP or (market, line) not in mk:
                continue
            for side, (price, point) in mk[(market, line)].items():
                q = fair.get(side)
                if q is None or price < MAX_FAVORITE:
                    continue
                ev_ = q * american_to_decimal(price) - 1
                if MIN_EV <= ev_ <= MAX_EV:
                    out.append({"market": market, "side": side, "point": point, "book": book, "price": price,
                                "fair": q, "ev": ev_, "sharp_price": sides[side][0]})
    return out


def cards(cfg: SportConfig, events: list[dict], espn_games: list, now: datetime,
          paused: bool = False) -> list[Card]:
    """The best sharp-value bet per game, matched to its ESPN game (so it is graded and tracked)."""
    if paused:
        return []
    by_teams = {}
    for g in espn_games:
        by_teams.setdefault((g.home, g.away), []).append(g)
    matcher = NameMatcher({t for pair in by_teams for t in pair})
    out = []
    for ev in events:
        start = datetime.fromisoformat(ev["commence_time"].replace("Z", "+00:00"))
        if start <= now or start > now + timedelta(hours=36):
            continue
        home, away = matcher.match(ev["home_team"]), matcher.match(ev["away_team"])
        game = next((g for g in by_teams.get((home, away), [])
                     if abs((g.date - start).total_seconds()) < 6 * 3600), None)
        if game is None:
            continue
        found = find_value(cfg, ev, now)
        if not found:
            continue
        v = max(found, key=lambda x: x["ev"])
        team = game.home if v["side"] == "home" else game.away if v["side"] == "away" else "Draw"
        if v["market"] == "h2h":
            selection, label = (f"{team} ML" if team != "Draw" else "Draw"), "Moneyline"
        else:
            selection, label = f"{team} {v['point']:+g}", "Spread"
        dec = american_to_decimal(v["price"])
        kelly = 0.25 * kelly_fraction(v["fair"], 1 - v["fair"], dec)
        book = BOOK_NAMES.get(v["book"], v["book"])
        pick = Pick(
            sport=cfg.key, event_id=game.event_id, commence_time=game.date, matchup=f"{game.away} @ {game.home}",
            market=v["market"], market_label=label, selection=selection, side=v["side"], point=v["point"],
            player=None, book=book, american=v["price"], model_prob=v["fair"], market_prob=v["fair"],
            win_prob=v["fair"], push_prob=0.0, fair_american=round(decimal_to_american(1 / v["fair"])),
            ev=v["ev"], kelly=kelly, stake=None, projection="", books=len(_prices(ev)),
        )
        units = min(max(round(kelly * 100 * 2) / 2, 0.5), MAX_UNITS)
        headline = (f"{book} pays {v['price']:+.0f}; Pinnacle's fair price is {pick.fair_american:+.0f} "
                    f"({v['fair']:.0%}). That's a {v['ev']:.1%} edge.")
        reasons = [
            f"Pinnacle (the sharpest book) has {selection} at {v['sharp_price']:+.0f}; without its margin that's "
            f"{v['fair']:.1%} to win.",
            f"{book} pays {v['price']:+.0f}: {v['ev']:+.1%} expected value per bet.",
            "Bet it at this book at this number or better. If the line moves, the edge is gone.",
        ]
        out.append(Card(tier="sharp", units=units, headline=headline, reasons=reasons, sport=cfg.key,
                        sport_name=cfg.name, pick=pick, home=game.home, away=game.away))
    return out


def run(sports: list[SportConfig], provider, now: datetime, clv_trust: dict | None = None,
        client: SharpClient | None = None, force: bool = False) -> list[Card]:
    """One check across the sports in season, within the free plan's limit.

    The sports list and ESPN schedules are free; a request is only spent on a sport in season
    with a game starting soon. Extra (non-core) checks only run when the budget allows."""
    client = client or SharpClient()
    if not client.api_key:
        return []
    try:
        active = client.active()  # free, and it reports the requests left this month
    except requests.RequestException as exc:
        log.warning("Odds API unavailable: %s", exc)
        return []
    todo = []
    for cfg in sports:
        if cfg.key not in SPORTS or cfg.odds_key not in active:
            continue
        try:
            games = provider.game_results(cfg, now - timedelta(hours=6), now + timedelta(days=2))
        except requests.RequestException as exc:
            log.warning("sharp value %s schedule failed: %s", cfg.key, exc)
            continue
        if any(now < g.date <= now + timedelta(hours=UPCOMING_HOURS) for g in games):
            todo.append((cfg, games))
    core = force or is_core(now)
    if not affordable(client.remaining, len(todo) * CREDITS_PER_SPORT, now, core):
        left = "unknown" if client.remaining is None else f"{client.remaining:.0f}"
        print(f"sharp value: extra check skipped to save the month's requests ({left} left)")
        return []
    out = []
    for cfg, games in todo:
        if client.remaining is not None and client.remaining - CREDITS_PER_SPORT < CREDIT_FLOOR:
            print(f"sharp value: stopping, {client.remaining:.0f} Odds API requests left this month")
            break
        try:
            events = client.odds(cfg)
        except requests.RequestException as exc:
            log.warning("sharp value %s failed: %s", cfg.key, exc)
            continue
        paused = (clv_trust or {}).get(f"{cfg.key}:sharp") == 0.0
        out += cards(cfg, events, games, now, paused=paused)
    if client.remaining is not None:
        print(f"sharp value: {len(out)} picks from {len(todo)} sports; {client.remaining:.0f} Odds API requests left this month")
    return out


# ---------------------------------------------------------------------- tennis
# The tennis blind test (tennishist, 2012+ ATP/WTA closing odds) found the best price across ~40
# books beating Pinnacle's no-vig price by 2%-15% made money (+6.8% blind, STRONG), but that
# "best" is often a stale price at an obscure book. The test closest to what we can bet, one big
# book (Bet365) against Pinnacle, did not prove out (-0.6% tuning, +2.7% blind). So tennis value
# picks carry no units until their own graded record proves out (Ledger.tennis_proven: 60+
# picks, ROI minus one standard error above zero, the older half profitable too).
# Each live tournament is one request a check, so tennis runs on core checks only and
# on at most TENNIS_MAX_KEYS tournaments.
TENNIS_PREFIXES = ("tennis_atp_", "tennis_wta_")
TENNIS_MAX_KEYS = 3
TENNIS_HOURS = 24
TENNIS_MAX_UNITS = 0.5


def tennis_cards(key: str, events: list[dict], now: datetime, proven: bool = False,
                 scheduled: set | None = None, rows: list[dict] | None = None) -> list[Card]:
    """``scheduled``: player pairs on ESPN's schedule. A match the books list but ESPN doesn't is
    skipped: late draw changes (a lucky loser in, a withdrawal) leave stale prices behind."""
    from .tennis import norm

    tour = "atp" if key.startswith("tennis_atp") else "wta"
    cfg = SimpleNamespace(key=f"tennis_{tour}", draws=False)
    out = []
    for ev in events:
        start = datetime.fromisoformat(ev["commence_time"].replace("Z", "+00:00"))
        if start <= now or start > now + timedelta(hours=TENNIS_HOURS):
            continue
        home, away = ev["home_team"], ev["away_team"]
        if scheduled is not None and tuple(sorted((norm(home), norm(away)))) not in scheduled:
            continue
        found = [v for v in find_value(cfg, ev, now) if v["market"] == "h2h"]
        if not found:
            continue
        v = max(found, key=lambda x: x["ev"])
        player = home if v["side"] == "home" else away
        dec = american_to_decimal(v["price"])
        kelly = 0.25 * kelly_fraction(v["fair"], 1 - v["fair"], dec)
        book = BOOK_NAMES.get(v["book"], v["book"])
        pick = Pick(
            sport=cfg.key, event_id=f"{norm(away)}|{norm(home)}", commence_time=start, matchup=f"{away} vs {home}",
            market="h2h", market_label="Moneyline", selection=f"{player} ML", side=v["side"], point=None,
            player=None, book=book, american=v["price"], model_prob=v["fair"], market_prob=v["fair"],
            win_prob=v["fair"], push_prob=0.0, fair_american=round(decimal_to_american(1 / v["fair"])),
            ev=v["ev"], kelly=kelly, stake=None, projection="", books=len(_prices(ev)),
        )
        units = min(max(round(kelly * 100 * 2) / 2, 0.5), TENNIS_MAX_UNITS) if proven else 0.0
        headline = (f"{book} pays {v['price']:+.0f} on {player}; Pinnacle's fair price is {pick.fair_american:+.0f} "
                    f"({v['fair']:.0%}). That's a {v['ev']:.1%} edge.")
        reasons = [
            f"Pinnacle (the sharpest book) has {player} at {v['sharp_price']:+.0f}; without its margin that's "
            f"{v['fair']:.1%} to win.",
            f"{book} pays {v['price']:+.0f}: {v['ev']:+.1%} expected value per bet.",
            f"Win chance {v['fair']:.0%}: this is a price edge, not a prediction that {player} wins. "
            f"Even a good price loses {1 - v['fair']:.0%} of the time; the edge only shows over many bets.",
            ("Units: our tennis value picks proved out on their own graded record." if proven else
             "No units yet: the 2012+ test of one big book (Bet365) against Pinnacle did not prove out, so tennis "
             "value picks stay leans until their own record does (60+ picks, profitable beyond luck)."),
            "Bet it at this book at this number or better. If the line moves, the edge is gone. "
            "The match must start for the bet to stand (retirement rules vary by book).",
        ]
        if rows:
            from .insights import MAX_FACTS
            from .tennis import match_facts

            facts = match_facts(rows, away, home, start, tour)
            reasons = reasons[:3] + facts[:max(MAX_FACTS - len(reasons), 8)] + reasons[3:]
        out.append(Card(tier="sharp", units=units, headline=headline, reasons=reasons, sport=cfg.key,
                        sport_name=f"Tennis · {tour.upper()}", pick=pick, home=home, away=away))
    return out


def run_tennis(now: datetime, client: SharpClient | None = None, force: bool = False,
               clv_trust: dict | None = None, proven: bool = False, scheduled: set | None = None,
               rows: list[dict] | None = None) -> list[Card]:
    """Tennis sharp value on the core checks (or when forced), a few tournaments at most."""
    client = client or SharpClient()
    if not client.api_key or not (force or is_core(now)) or scheduled is None:
        return []  # no ESPN schedule to confirm the draw: no tennis picks
    try:
        active = sorted(k for k in client.active() if k.startswith(TENNIS_PREFIXES))
    except requests.RequestException as exc:
        log.warning("Odds API unavailable: %s", exc)
        return []
    out = []
    for key in active[:TENNIS_MAX_KEYS]:
        if client.remaining is not None and client.remaining - CREDITS_PER_SPORT < CREDIT_FLOOR:
            print(f"tennis sharp value: stopping, {client.remaining:.0f} Odds API requests left this month")
            break
        tour = "atp" if key.startswith("tennis_atp") else "wta"
        if (clv_trust or {}).get(f"tennis_{tour}:sharp") == 0.0:
            continue
        try:
            events = client.odds(SimpleNamespace(odds_key=key))
        except requests.RequestException as exc:
            log.warning("tennis sharp value %s failed: %s", key, exc)
            continue
        out += tennis_cards(key, events, now, proven=proven, scheduled=scheduled, rows=rows)
    print(f"tennis sharp value: {len(out)} picks from {min(len(active), TENNIS_MAX_KEYS)} tournaments"
          + (f"; {client.remaining:.0f} requests left" if client.remaining is not None else ""))
    return out
