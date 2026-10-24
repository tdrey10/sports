"""Elo ratings and the home-minus-away model inputs, replayed game by game (pre-game only).

Spec (D503 part 2, sections 3-4): Elo from 1500 with a learned home edge (0 at neutral sites), a
margin-of-victory multiplier, and a one-time pull of 1/3 toward 1500 after a 75+ day break. The
inputs are elo, form, rest, b2b, inj, key, revenge, letdown, bye, short, intl, alt, cold, weather,
travel. inj and key need injury / player history we don't hold for past seasons, so they are 0
in training and filled from today's injury report on the live board.
"""

from __future__ import annotations

import math
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime

from .data import Game

FEATURES = ("elo", "form", "rest", "b2b", "inj", "key", "revenge", "letdown", "bye", "short", "intl", "alt",
            "cold", "weather", "travel")
FOOTBALL = ("nfl", "ncaaf")
BREAK_DAYS = 75
BIG_WIN = {"nfl": 17, "ncaaf": 21, "nba": 15, "ncaab": 15, "mlb": 5, "nhl": 3}
BASE_K = {"nfl": 20, "ncaaf": 25, "nba": 20, "mlb": 4, "nhl": 6, "ncaab": 22}
DEFAULT_HFA = {"nfl": 48, "ncaaf": 60, "nba": 70, "mlb": 24, "nhl": 30, "ncaab": 80}
MIN_KNOWN = 5


def logit(p: float) -> float:
    p = min(max(p, 1e-6), 1 - 1e-6)
    return math.log(p / (1 - p))


def sigmoid(x: float) -> float:
    return 1 / (1 + math.exp(-x)) if x > -40 else 0.0


def p_elo(rh: float, ra: float, hfa: float, neutral: bool) -> float:
    return 1 / (1 + 10 ** (-(rh - ra + (0 if neutral else hfa)) / 400))


@dataclass
class TeamState:
    rating: float = 1500.0
    games: int = 0
    last: datetime | None = None
    last_margin: float | None = None  # own score minus opponent's, last game
    last_won: bool | None = None
    form: deque = field(default_factory=lambda: deque(maxlen=10))  # result - p_elo
    home_elev: float | None = None
    home_temp: float | None = None
    home_tz: float | None = None
    streak: int = 0  # + wins in a row, - losses in a row
    season_w: int = 0
    season_l: int = 0


class Replay:
    """Walks games in time order. ``features(g)`` reads the state BEFORE ``g``; ``update(g)`` applies a final."""

    def __init__(self, sport: str, k: float | None = None, hfa: float | None = None):
        self.sport = sport
        self.k = BASE_K[sport] if k is None else k
        self.hfa = DEFAULT_HFA[sport] if hfa is None else hfa
        self.teams: dict[str, TeamState] = defaultdict(TeamState)
        self.last_meeting: dict[frozenset, str | None] = {}  # pair -> winner of their last meeting

    # ------------------------------------------------------------------ helpers
    def _rest(self, t: TeamState, when: datetime) -> float:
        if t.last is None:
            return 10.0
        return min(10.0, (when - t.last).total_seconds() / 86400)

    def _break(self, t: TeamState, when: datetime) -> None:
        if t.last is not None and (when - t.last).days >= BREAK_DAYS:
            t.rating = 1500 + (t.rating - 1500) * 2 / 3
            t.form.clear()
            t.streak = 0
            t.season_w = t.season_l = 0

    def ratings(self, g: Game) -> tuple[float, float]:
        h, a = self.teams[g.home], self.teams[g.away]
        for t in (h, a):
            self._break(t, g.start)
        return h.rating, a.rating

    # ------------------------------------------------------------------ features
    def features(self, g: Game, inj_out: tuple[int, int] = (0, 0), key: float = 0.0) -> dict | None:
        h, a = self.teams[g.home], self.teams[g.away]
        rh, ra = self.ratings(g)
        pe = p_elo(rh, ra, self.hfa, g.neutral)
        rest_h, rest_a = self._rest(h, g.start), self._rest(a, g.start)
        football = self.sport in FOOTBALL
        f = {k: 0.0 for k in FEATURES}
        f["elo"] = logit(pe)
        mean = lambda d: sum(d) / len(d) if d else 0.0  # noqa: E731
        f["form"] = mean(h.form) - mean(a.form)
        f["rest"] = (rest_h - rest_a) / 7
        f["b2b"] = float(rest_a <= 1.2) - float(rest_h <= 1.2)
        f["inj"] = (inj_out[1] - inj_out[0]) / 5
        f["key"] = key
        last = self.last_meeting.get(frozenset((g.home, g.away)))
        if last:
            f["revenge"] = float(last == g.away) - float(last == g.home)
        big = BIG_WIN[self.sport]
        won_big = lambda t: t.last_margin is not None and t.last_margin >= big  # noqa: E731
        f["letdown"] = float(won_big(a)) - float(won_big(h))
        if football:
            f["bye"] = float(rest_h >= 10 and h.last is not None) - float(rest_a >= 10 and a.last is not None)
            f["short"] = float(rest_a <= 5) - float(rest_h <= 5)
        f["intl"] = float(g.intl)
        if g.elev is not None and g.elev >= 1000:
            thin = lambda t: max(0.0, g.elev - (t.home_elev if t.home_elev is not None else g.elev)) / 1000  # noqa: E731
            f["alt"] = thin(a) - thin(h)
        if g.temp is not None and g.temp < 45:
            shock = lambda t: max(0.0, (t.home_temp if t.home_temp is not None else 70) - g.temp) / 30  # noqa: E731
            f["cold"] = shock(a) - shock(h)
        if g.temp is not None:
            nasty = max(0.0, (g.wind or 0) - 15) / 10 + min(g.rain or 0, 20) / 10
            f["weather"] = nasty * (1 if rh + (0 if g.neutral else self.hfa) < ra else -1)
        if g.tzo is not None:
            lag = lambda t: abs(g.tzo - t.home_tz) / 3 if t.home_tz is not None else 0.0  # noqa: E731
            f["travel"] = lag(a) - lag(h)
        known = min(h.games, a.games)
        return {"f": f, "p_elo": pe, "known": known, "rest_h": rest_h, "rest_a": rest_a,
                "elo_pts": rh - ra + (0 if g.neutral else self.hfa), "rh": rh, "ra": ra}

    # ------------------------------------------------------------------ results
    def update(self, g: Game) -> None:
        if not g.done:
            return
        h, a = self.teams[g.home], self.teams[g.away]
        rh, ra = self.ratings(g)
        pe = p_elo(rh, ra, self.hfa, g.neutral)
        res = 1.0 if g.hs > g.as_ else 0.0 if g.hs < g.as_ else 0.5
        margin = abs(g.hs - g.as_)
        elo_pts = rh - ra + (0 if g.neutral else self.hfa)
        diff_w = elo_pts * (1 if res >= 0.5 else -1)
        mult = 1.0 if margin == 0 else math.log(margin + 1) * 2.2 / (diff_w * 0.001 + 2.2)
        d = self.k * mult * (res - pe)
        h.rating += d
        a.rating -= d
        h.form.append(res - pe)
        a.form.append((1 - res) - (1 - pe))
        for t, m, won in ((h, g.hs - g.as_, res == 1.0), (a, g.as_ - g.hs, res == 0.0)):
            t.games += 1
            t.last = g.start
            t.last_margin = m
            t.last_won = won if res != 0.5 else None
            if res == 0.5:
                t.streak = 0
            elif won:
                t.streak = t.streak + 1 if t.streak > 0 else 1
                t.season_w += 1
            else:
                t.streak = t.streak - 1 if t.streak < 0 else -1
                t.season_l += 1
        if not g.neutral and not g.intl:
            if g.elev is not None:
                h.home_elev = g.elev if h.home_elev is None else 0.8 * h.home_elev + 0.2 * g.elev
            temp = 70.0 if (g.roof or "").lower() in ("dome", "closed") else g.temp
            if temp is not None:
                h.home_temp = temp if h.home_temp is None else 0.9 * h.home_temp + 0.1 * temp
            if g.tzo is not None:
                h.home_tz = g.tzo
        winner = g.home if res == 1.0 else g.away if res == 0.0 else None
        self.last_meeting[frozenset((g.home, g.away))] = winner


def replay(games: list[Game], sport: str, k: float | None = None, hfa: float | None = None):
    """Yield (game, pre-game features) for every game in order, applying finals as it goes."""
    r = Replay(sport, k, hfa)
    for g in games:
        yield g, r.features(g)
        r.update(g)


def tune_elo(games: list[Game], sport: str, last: int = 900) -> tuple[float, float]:
    """K and home edge with the lowest log loss of p_elo on the most recent ``last`` finished games."""
    done = [g for g in games if g.done and g.hs != g.as_]
    best = None
    for km in (0.5, 0.75, 1, 1.5, 2):
        for hfa in (0, 20, 40, 60, 80, 100):
            k = BASE_K[sport] * km
            r = Replay(sport, k, hfa)
            ll, n = 0.0, 0
            cut = len(done) - last
            for i, g in enumerate(done):
                if i >= max(cut, 30):
                    rh, ra = r.ratings(g)
                    p = p_elo(rh, ra, hfa, g.neutral)
                    y = g.hs > g.as_
                    ll -= math.log(max(p if y else 1 - p, 1e-9))
                    n += 1
                r.update(g)
            if n and (best is None or ll / n < best[0]):
                best = (ll / n, k, hfa)
    return (best[1], best[2]) if best else (BASE_K[sport], DEFAULT_HFA[sport])


__all__ = ["Replay", "replay", "tune_elo", "FEATURES", "logit", "sigmoid", "p_elo"]
