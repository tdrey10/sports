"""Sharp value: regular sportsbooks priced better than Pinnacle's fair line.

Pinnacle takes big bets from professionals, so its no-vig price is about the most
accurate probability there is. When DraftKings, FanDuel, BetMGM or another US book
pays more than that fair price by 2%+ (and -170 or better), the bet is +EV without
relying on our own model. It's the standard method of value-betting software.

Odds come from The Odds API (free plan: 500 requests a month, key in
ODDS_API_KEY). One check of one sport's moneylines and spreads across Pinnacle and
up to nine US books costs 1 request (moneylines), so checks run twice a day for the
big five in season (about 10 requests a day) and stop well before the monthly limit. Each pick is matched to its ESPN
game so it is graded and its closing line value tracked like every other pick; the
adaptive layer pauses sharp value in a sport whose picks keep losing to the close.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta

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
CHECK_HOURS_UTC = (19, 0)  # about noon and 5 PM Pacific
CREDIT_FLOOR = 40
CREDITS_PER_SPORT = 1  # one market, up to ten books


def due(now: datetime) -> bool:
    """Twice a day: the first run in each check hour."""
    return now.hour in CHECK_HOURS_UTC and now.minute < 35


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
            "Bet it at this book and price or better; the edge is gone if the price moves.",
        ]
        out.append(Card(tier="sharp", units=units, headline=headline, reasons=reasons, sport=cfg.key,
                        sport_name=cfg.name, pick=pick, home=game.home, away=game.away))
    return out


def run(sports: list[SportConfig], provider, now: datetime, clv_trust: dict | None = None,
        client: SharpClient | None = None) -> list[Card]:
    """One check across the sports in season, within the free plan's limit."""
    client = client or SharpClient()
    if not client.api_key:
        return []
    try:
        active = client.active()
    except requests.RequestException as exc:
        log.warning("Odds API unavailable: %s", exc)
        return []
    out = []
    for cfg in sports:
        if cfg.key not in SPORTS or cfg.odds_key not in active:
            continue
        if client.remaining is not None and client.remaining - CREDITS_PER_SPORT < CREDIT_FLOOR:
            print(f"sharp value: stopping, {client.remaining:.0f} Odds API requests left this month")
            break
        try:
            events = client.odds(cfg)
            games = provider.game_results(cfg, now - timedelta(hours=6), now + timedelta(days=2))
        except requests.RequestException as exc:
            log.warning("sharp value %s failed: %s", cfg.key, exc)
            continue
        paused = (clv_trust or {}).get(f"{cfg.key}:sharp") == 0.0
        out += cards(cfg, events, games, now, paused=paused)
    if client.remaining is not None:
        print(f"sharp value: {len(out)} picks; {client.remaining:.0f} Odds API requests left this month")
    return out
