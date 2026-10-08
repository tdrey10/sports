"""The Odds API (https://the-odds-api.com) client for game lines and props.

Needs an API key in ``ODDS_API_KEY``. Game lines for a whole sport cost one
request per market/region; player props are fetched per event and cost more,
so props are opt-in in the CLI.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import requests

from ..sports import SportConfig
from .base import Event, Quote
from .espn import parse_date

log = logging.getLogger(__name__)

BASE = "https://api.the-odds-api.com/v4"
GAME_MARKETS = ("h2h", "spreads", "totals")


class OddsApiClient:
    def __init__(
        self,
        api_key: str | None = None,
        regions: str = "us",
        bookmakers: list[str] | None = None,
        session: requests.Session | None = None,
        timeout: float = 20,
    ):
        self.api_key = api_key or os.environ.get("ODDS_API_KEY")
        if not self.api_key:
            raise RuntimeError("Set ODDS_API_KEY (get a key at https://the-odds-api.com)")
        self.regions = regions
        self.bookmakers = bookmakers
        self.session = session or requests.Session()
        self.timeout = timeout
        self.requests_remaining: str | None = None

    def _get(self, path: str, params: dict[str, Any]) -> Any:
        params = {**params, "apiKey": self.api_key, "oddsFormat": "american"}
        if self.bookmakers:
            params["bookmakers"] = ",".join(self.bookmakers)
        else:
            params["regions"] = self.regions
        resp = self.session.get(f"{BASE}{path}", params=params, timeout=self.timeout)
        self.requests_remaining = resp.headers.get("x-requests-remaining", self.requests_remaining)
        resp.raise_for_status()
        return resp.json()

    def active_keys(self) -> set[str]:
        """Sport keys currently in season (this endpoint costs no credits)."""
        resp = self.session.get(f"{BASE}/sports", params={"apiKey": self.api_key}, timeout=self.timeout)
        self.requests_remaining = resp.headers.get("x-requests-remaining", self.requests_remaining)
        resp.raise_for_status()
        return {s["key"] for s in resp.json() if s.get("active")}

    def events(self, cfg: SportConfig) -> list[Event]:
        data = self._get(f"/sports/{cfg.odds_key}/odds", {"markets": ",".join(GAME_MARKETS)})
        return [parse_event(cfg, ev) for ev in data]

    def prop_quotes(self, cfg: SportConfig, event: Event, markets: list[str]) -> list[Quote]:
        if not markets:
            return []
        try:
            data = self._get(f"/sports/{cfg.odds_key}/events/{event.id}/odds", {"markets": ",".join(markets)})
        except requests.HTTPError as exc:
            log.warning("props for %s unavailable: %s", event.id, exc)
            return []
        return parse_event(cfg, data).quotes


def parse_event(cfg: SportConfig, ev: dict) -> Event:
    home, away = ev["home_team"], ev["away_team"]
    event = Event(
        id=ev["id"],
        sport=cfg.key,
        commence_time=parse_date(ev["commence_time"]),
        home=home,
        away=away,
    )
    for book in ev.get("bookmakers", []):
        for market in book.get("markets", []):
            key = market["key"]
            for o in market.get("outcomes", []):
                name = o.get("name", "")
                player = None
                if key in GAME_MARKETS:
                    if name == home:
                        side = "home"
                    elif name == away:
                        side = "away"
                    elif name.lower() == "draw":
                        side = "draw"
                    else:
                        side = name.lower()  # over / under
                else:
                    side = name.lower()
                    player = o.get("description")
                    if side not in ("over", "under") or not player:
                        continue  # skip yes/no markets like anytime scorer
                event.quotes.append(
                    Quote(
                        market=key,
                        side=side,
                        point=o.get("point"),
                        book=book["key"],
                        american=float(o["price"]),
                        player=player,
                    )
                )
    return event
