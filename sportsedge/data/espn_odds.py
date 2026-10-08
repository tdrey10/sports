"""Free betting odds from ESPN (DraftKings lines, plus bet365 for soccer).

ESPN publishes, at no cost:
- pregame moneyline / spread / total prices per game (core API, provider 100),
- live in-game prices for games in progress (provider 200),
- player prop lines (the number only, no price) per game.

Prop prices aren't published, so props are priced at an assumed -115 each way
(a typical DraftKings prop price). Those picks say so, and you should only take
them at -115 or better.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone

import requests

from ..models.ratings import GameResult
from ..odds import decimal_to_american
from ..sports import SportConfig
from .base import Event, Quote
from .espn import EspnClient

log = logging.getLogger(__name__)

CORE = "https://sports.core.api.espn.com/v2/sports"
PREGAME_PROVIDER = "100"
LIVE_PROVIDER = "200"
ASSUMED_PROP_PRICE = -115.0
ESTIMATED_BOOK = "draftkings_est"

# ESPN prop type name -> our market key, matched in order on the lowercased name.
_PROP_RULES: dict[str, list[tuple[tuple[str, ...], tuple[str, ...], str]]] = {
    # (must contain all, must contain none, market)
    "football": [
        (("passing", "yards"), ("plus", "+", "longest"), "player_pass_yds"),
        (("rushing", "yards"), ("plus", "+", "longest"), "player_rush_yds"),
        (("receiving", "yards"), ("plus", "+", "longest"), "player_reception_yds"),
        (("receptions",), (), "player_receptions"),
        (("completions",), (), "player_pass_completions"),
        (("pass", "attempts"), (), "player_pass_attempts"),
        (("passing", "attempts"), (), "player_pass_attempts"),
        (("rushing", "attempts"), (), "player_rush_attempts"),
        (("interceptions",), (), "player_pass_interceptions"),
        (("passing", "touchdowns"), (), "player_pass_tds"),
        (("touchdown", "passes"), (), "player_pass_tds"),
    ],
    "basketball": [
        (("points", "rebounds", "assists"), (), "player_points_rebounds_assists"),
        (("points", "rebounds"), (), "player_points_rebounds"),
        (("points", "assists"), (), "player_points_assists"),
        (("rebounds", "assists"), (), "player_rebounds_assists"),
        (("3-point",), (), "player_threes"),
        (("three",), (), "player_threes"),
        (("points",), ("team",), "player_points"),
        (("rebounds",), (), "player_rebounds"),
        (("assists",), (), "player_assists"),
        (("steals",), (), "player_steals"),
        (("blocks",), (), "player_blocks"),
        (("turnovers",), (), "player_turnovers"),
    ],
    "hockey": [
        (("shots on goal",), (), "player_shots_on_goal"),
        (("blocked shots",), (), "player_blocked_shots"),
        (("saves",), (), "player_total_saves"),
        (("assists",), (), "player_assists"),
        (("goals",), ("against",), "player_goals"),
        (("points",), ("team", "power"), "player_points"),
    ],
    "baseball": [
        (("strikeouts",), ("batter",), "pitcher_strikeouts"),
        (("hits allowed",), (), "pitcher_hits_allowed"),
        (("earned runs",), (), "pitcher_earned_runs"),
        (("outs",), (), "pitcher_outs"),
        (("hits", "runs", "rbis"), (), "batter_hits_runs_rbis"),
        (("home runs",), (), "batter_home_runs"),
        (("rbis",), (), "batter_rbis"),
        (("runs",), ("earned", "home"), "batter_runs_scored"),
        (("hits",), (), "batter_hits"),
    ],
}
_PERIOD_WORDS = ("1st", "2nd", "3rd", "4th", "quarter", "half", "period", "inning", "milestone", "first", "last")


def prop_market(sport_family: str, type_name: str) -> str | None:
    name = (type_name or "").lower()
    if any(w in name for w in _PERIOD_WORDS):
        return None
    for need, avoid, market in _PROP_RULES.get(sport_family, []):
        if all(n in name for n in need) and not any(a in name for a in avoid):
            return market
    return None


def _book(provider: dict) -> str:
    name = (provider.get("name") or provider.get("displayName") or "").lower()
    key = re.sub(r"[^a-z0-9]", "", name.replace("- live odds", ""))
    return {"draftkings": "draftkings", "bet365": "bet365"}.get(key, key or "espn")


def _num(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _side_price(side: dict, key: str) -> float | None:
    """American price from DraftKings-style (moneyLine/spreadOdds) or bet365-style (decimal) objects."""
    price = _num(side.get(key))
    if price is not None and price != 0:
        return price
    if key == "moneyLine":
        dec = _num((side.get("odds") or {}).get("value"))
        if dec and dec > 1:
            return float(decimal_to_american(dec))
    return None


def parse_provider_odds(o: dict, book: str | None = None) -> list[Quote]:
    """Quotes from one ESPN core odds object (a single sportsbook)."""
    book = book or _book(o.get("provider") or {})
    home, away = o.get("homeTeamOdds") or {}, o.get("awayTeamOdds") or {}
    quotes: list[Quote] = []
    hml, aml = _side_price(home, "moneyLine"), _side_price(away, "moneyLine")
    if hml is not None and aml is not None:
        quotes += [Quote("h2h", "home", None, book, hml), Quote("h2h", "away", None, book, aml)]
        dml = _side_price(o.get("drawOdds") or {}, "moneyLine")
        if dml is not None:
            quotes.append(Quote("h2h", "draw", None, book, dml))
    spread = _num(o.get("spread"))
    hs, as_ = _side_price(home, "spreadOdds"), _side_price(away, "spreadOdds")
    if spread is not None and hs is not None and as_ is not None:
        quotes += [Quote("spreads", "home", spread, book, hs), Quote("spreads", "away", -spread, book, as_)]
    total = _num(o.get("overUnder"))
    over, under = _num(o.get("overOdds")), _num(o.get("underOdds"))
    if total is not None and over and under:
        quotes += [Quote("totals", "over", total, book, over), Quote("totals", "under", total, book, under)]
    return quotes


OPEN_BOOK = "draftkings_open"


def _american(v) -> float | None:
    """'+114', '-110', 'EVEN' or a number -> American odds."""
    if isinstance(v, dict):
        v = v.get("american") or v.get("alternateDisplayValue")
    if v is None:
        return None
    if str(v).strip().upper() in ("EVEN", "EV", "PK"):
        return 100.0
    return _num(str(v).replace("+", "").strip())


def parse_open_odds(o: dict) -> list[Quote]:
    """The opening DraftKings prices ESPN keeps next to the close (moneyline and spread)."""
    home = (o.get("homeTeamOdds") or {}).get("open") or {}
    away = (o.get("awayTeamOdds") or {}).get("open") or {}
    quotes: list[Quote] = []
    hml, aml = _american(home.get("moneyLine")), _american(away.get("moneyLine"))
    if hml and aml:
        quotes += [Quote("h2h", "home", None, OPEN_BOOK, hml), Quote("h2h", "away", None, OPEN_BOOK, aml)]
        dml = _american(((o.get("drawOdds") or {}).get("open") or {}).get("moneyLine"))
        if dml:
            quotes.append(Quote("h2h", "draw", None, OPEN_BOOK, dml))
    hp, ap = _american(home.get("pointSpread")), _american(away.get("pointSpread"))
    hs, as_ = _american(home.get("spread")), _american(away.get("spread"))
    if hp is not None and ap is not None and hs and as_:
        quotes += [Quote("spreads", "home", hp, OPEN_BOOK, hs), Quote("spreads", "away", ap, OPEN_BOOK, as_)]
    return quotes


class EspnOdds:
    def __init__(self, espn: EspnClient, prop_price: float = ASSUMED_PROP_PRICE):
        self.espn = espn
        self.prop_price = prop_price

    def _core_base(self, cfg: SportConfig, event_id: str) -> str:
        sport, league = cfg.espn_path.split("/", 1)
        return f"{CORE}/{sport}/leagues/{league}/events/{event_id}/competitions/{event_id}/odds"

    def _get(self, url: str, ttl: float | None, **params) -> dict:
        try:
            return self.espn._get(url, params, ttl=ttl)
        except requests.RequestException as exc:
            log.info("ESPN odds %s unavailable: %s", url, exc)
            return {}

    def game_quotes(self, cfg: SportConfig, event_id: str, live: bool = False) -> list[Quote]:
        listing = self._get(self._core_base(cfg, event_id), ttl=0 if live else 600)
        quotes: list[Quote] = []
        for item in listing.get("items", []) or []:
            ref = item.get("$ref", "")
            pid = ref.rstrip("/").split("?")[0].rsplit("/", 1)[-1]
            if (pid == LIVE_PROVIDER) != live:
                continue
            o = self._get(ref.replace("http://", "https://"), ttl=0 if live else 600)
            quotes += parse_provider_odds(o)
        return quotes

    def closing_quotes(self, cfg: SportConfig, event_id: str) -> list[Quote] | None:
        """Closing pregame lines for a finished game (ESPN keeps them: the top-level
        prices on a completed game are the close). Cached for good: they never change.

        Only the DraftKings pregame provider is used. Other providers on a finished
        game (bet365 on soccer, live odds) hold the final in-game prices, e.g. +50000
        on the side that lost, which would wreck any study."""
        # One request: the DraftKings pregame object directly. 404 = ESPN has no odds
        # for that game (saved as none, never asked again); other errors = try later.
        # Usually one request: the DraftKings pregame object directly. Some leagues (MLB)
        # only list it inline, so a 404 falls back to the listing. Nothing in either =
        # ESPN has no odds for that game (saved as none); network errors = try later.
        base = self._core_base(cfg, event_id)
        try:
            o = self.espn._get(f"{base}/{PREGAME_PROVIDER}", {}, ttl=None)
        except requests.HTTPError as exc:
            if exc.response is None or exc.response.status_code != 404:
                log.info("ESPN closing odds %s unavailable: %s", event_id, exc)
                return None
            o = None
        except requests.RequestException as exc:
            log.info("ESPN closing odds %s unavailable: %s", event_id, exc)
            return None
        if o is None:
            try:
                listing = self.espn._get(base, {}, ttl=None)
            except requests.HTTPError as exc:
                if exc.response is not None and exc.response.status_code == 404:
                    return []
                return None
            except requests.RequestException:
                return None
            for item in listing.get("items", []) or []:
                ref = item.get("$ref", "")
                pid = str((item.get("provider") or {}).get("id") or ref.rstrip("/").split("?")[0].rsplit("/", 1)[-1])
                if pid == PREGAME_PROVIDER:
                    o = item if "homeTeamOdds" in item else None
                    break
            if o is None:
                return []
        return parse_provider_odds(o, book="draftkings") + parse_open_odds(o)

    def _event(self, cfg: SportConfig, g: GameResult, quotes: list[Quote]) -> Event:
        return Event(id=g.event_id, sport=cfg.key, commence_time=g.date, home=g.home, away=g.away,
                     quotes=quotes, neutral=g.neutral)

    def events(self, cfg: SportConfig, days_ahead: float = 4) -> list[Event]:
        now = datetime.now(timezone.utc)
        games = self.espn.game_results(cfg, now - timedelta(hours=6), now + timedelta(days=days_ahead))
        upcoming = [g for g in games if not g.completed and g.state in ("", "pre") and g.date > now]
        out = []
        for g in upcoming:
            quotes = self.game_quotes(cfg, g.event_id)
            if quotes:
                out.append(self._event(cfg, g, quotes))
        return out

    def live_events(self, cfg: SportConfig, live_games: list[GameResult]) -> list[Event]:
        out = []
        for g in live_games:
            quotes = self.game_quotes(cfg, g.event_id, live=True)
            if quotes:
                out.append(self._event(cfg, g, quotes))
        return out

    def prop_quotes(self, cfg: SportConfig, event: Event, markets: list[str]) -> list[Quote]:
        family = cfg.espn_path.split("/", 1)[0]
        url = f"{self._core_base(cfg, event.id)}/{PREGAME_PROVIDER}/propBets"
        wanted = set(markets)
        seen: set[tuple[str, str]] = set()
        quotes: list[Quote] = []
        page, pages = 1, 1
        while page <= pages and page <= 5:
            data = self._get(url, ttl=1800, limit=1000, page=page)
            pages = int(data.get("pageCount") or 1)
            for item in data.get("items", []) or []:
                market = prop_market(family, (item.get("type") or {}).get("name", ""))
                if market is None or market not in wanted or "athlete" not in item:
                    continue
                line = _num(((item.get("current") or {}).get("target") or {}).get("value"))
                if line is None:
                    continue
                player = self._athlete_name(item["athlete"].get("$ref", ""))
                if not player or (player, market) in seen:
                    continue  # keep the main line, skip alternates
                seen.add((player, market))
                quotes += [
                    Quote(market, "over", line, ESTIMATED_BOOK, self.prop_price, player),
                    Quote(market, "under", line, ESTIMATED_BOOK, self.prop_price, player),
                ]
            page += 1
        return quotes

    def _athlete_name(self, ref: str) -> str | None:
        if not ref:
            return None
        data = self._get(ref.replace("http://", "https://").split("?")[0], ttl=None)
        return data.get("displayName") or data.get("fullName")
