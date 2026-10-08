"""Shared data types and the provider interface the engine consumes."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from difflib import SequenceMatcher
from typing import Iterable, Protocol

from ..models.props import PlayerGame
from ..models.ratings import GameResult
from ..sports import SportConfig


@dataclass(frozen=True)
class Quote:
    """One bookmaker's price on one side of one market."""

    market: str  # "h2h", "spreads", "totals" or a prop market key
    side: str  # "home" / "away" / "draw" / "over" / "under"
    point: float | None
    book: str
    american: float
    player: str | None = None


@dataclass
class Event:
    id: str
    sport: str
    commence_time: datetime
    home: str
    away: str
    quotes: list[Quote] = field(default_factory=list)
    neutral: bool = False


class DataProvider(Protocol):
    def game_results(self, cfg: SportConfig, start: datetime, end: datetime) -> list[GameResult]:
        """Completed and scheduled games (scheduled games have no scores)."""

    def events(self, cfg: SportConfig) -> list[Event]:
        """Upcoming events with game-line quotes (h2h, spreads, totals)."""

    def prop_quotes(self, cfg: SportConfig, event: Event, markets: list[str]) -> list[Quote]:
        """Player prop quotes for one event."""

    def player_games(self, cfg: SportConfig, results: list[GameResult], team: str) -> list[PlayerGame]:
        """Box-score lines for ``team``'s players from its recent games."""

    def box_score(self, cfg: SportConfig, game: GameResult) -> list[PlayerGame]:
        """Every player's line from one finished game (used to grade props)."""

    def injuries(self, cfg: SportConfig) -> dict[str, list[tuple[str, str]]]:
        """Team -> [(player, status)] from the current injury report."""


OUT_STATUSES = ("out", "injured reserve", "suspended", "doubtful", "ir", "day-to-day-out")


def is_out(status: str) -> bool:
    s = status.lower()
    return any(s.startswith(x) for x in OUT_STATUSES)


_SUFFIXES = re.compile(r"\b(jr|sr|ii|iii|iv|v)\b")


def normalise_name(name: str) -> str:
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    name = name.lower().replace("&", " and ").replace("st.", "state")
    name = re.sub(r"[^a-z0-9 ]", " ", name)
    name = _SUFFIXES.sub(" ", name)
    return " ".join(name.split())


class NameMatcher:
    """Map names from one source (odds) onto names from another (stats)."""

    def __init__(self, targets: Iterable[str], threshold: float = 0.82):
        self.targets = list(targets)
        self.norm = {normalise_name(t): t for t in self.targets}
        self.threshold = threshold
        self._cache: dict[str, str | None] = {}

    def match(self, name: str) -> str | None:
        if name in self._cache:
            return self._cache[name]
        key = normalise_name(name)
        result = self.norm.get(key)
        if result is None:
            best, best_score = None, 0.0
            for norm, original in self.norm.items():
                score = SequenceMatcher(None, key, norm).ratio()
                # Reward one name containing the other ("Man Utd" style aside,
                # this handles "Brighton" vs "Brighton and Hove Albion").
                if key and norm and (key in norm or norm in key):
                    score = max(score, 0.9)
                if score > best_score:
                    best, best_score = original, score
            if best_score >= self.threshold:
                result = best
        self._cache[name] = result
        return result
