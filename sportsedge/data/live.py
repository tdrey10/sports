"""Live provider: ESPN for stats, The Odds API for prices."""

from __future__ import annotations

from datetime import datetime

from ..models.props import PlayerGame
from ..models.ratings import GameResult
from ..sports import SportConfig
from .base import Event, Quote
from .espn import EspnClient
from .oddsapi import OddsApiClient


class LiveProvider:
    def __init__(self, espn: EspnClient | None = None, odds: OddsApiClient | None = None):
        self.espn = espn or EspnClient()
        self.odds = odds or OddsApiClient()

    def game_results(self, cfg: SportConfig, start: datetime, end: datetime) -> list[GameResult]:
        return self.espn.game_results(cfg, start, end)

    def events(self, cfg: SportConfig) -> list[Event]:
        return self.odds.events(cfg)

    def prop_quotes(self, cfg: SportConfig, event: Event, markets: list[str]) -> list[Quote]:
        return self.odds.prop_quotes(cfg, event, markets)

    def player_games(self, cfg: SportConfig, results: list[GameResult], team: str) -> list[PlayerGame]:
        return self.espn.player_games(cfg, results, team)

    def box_score(self, cfg: SportConfig, game: GameResult) -> list[PlayerGame]:
        return self.espn.box_score(cfg, game)

    def injuries(self, cfg: SportConfig) -> dict[str, list[tuple[str, str]]]:
        return self.espn.injuries(cfg)

    def live_games(self, cfg: SportConfig) -> list[GameResult]:
        return self.espn.live_games(cfg)


class EspnProvider:
    """Everything from ESPN: stats, game odds, live odds and prop lines. Free, no API key."""

    def __init__(self, espn: EspnClient | None = None):
        from .espn_odds import EspnOdds

        self.espn = espn or EspnClient()
        self.espn_odds = EspnOdds(self.espn)

    def game_results(self, cfg: SportConfig, start: datetime, end: datetime) -> list[GameResult]:
        return self.espn.game_results(cfg, start, end)

    def events(self, cfg: SportConfig) -> list[Event]:
        return self.espn_odds.events(cfg)

    def live_events(self, cfg: SportConfig, live_games: list[GameResult]) -> list[Event]:
        return self.espn_odds.live_events(cfg, live_games)

    def prop_quotes(self, cfg: SportConfig, event: Event, markets: list[str]) -> list[Quote]:
        return self.espn_odds.prop_quotes(cfg, event, markets)

    def closing_quotes(self, cfg: SportConfig, game: GameResult) -> list[Quote] | None:
        return self.espn_odds.closing_quotes(cfg, game.event_id)

    def player_games(self, cfg: SportConfig, results: list[GameResult], team: str) -> list[PlayerGame]:
        return self.espn.player_games(cfg, results, team)

    def box_score(self, cfg: SportConfig, game: GameResult) -> list[PlayerGame]:
        return self.espn.box_score(cfg, game)

    def injuries(self, cfg: SportConfig) -> dict[str, list[tuple[str, str]]]:
        return self.espn.injuries(cfg)

    def live_games(self, cfg: SportConfig) -> list[GameResult]:
        return self.espn.live_games(cfg)
