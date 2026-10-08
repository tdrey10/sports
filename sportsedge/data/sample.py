"""Synthetic data so the engine can run end to end with no network or API key.

Teams get hidden "true" strengths; past games are simulated from them; books
price upcoming games and props from noisy copies of those strengths. The
engine never sees the true values, so the demo shows the real workflow
(fit -> predict -> compare to prices). Edges it finds are not real bets.
"""

from __future__ import annotations

import hashlib
import math
import random
from datetime import datetime, timedelta, timezone

from ..distributions import count_pmf, discrete_normal_pmf, line_probs, negate
from ..models.game import predict_game
from ..models.props import PlayerGame
from ..models.ratings import GameResult, TeamRatings
from ..odds import decimal_to_american
from ..sports import SportConfig
from .base import Event, Quote

# (base score, home advantage, team spread) in score units, or log units for count sports
_SCORING = {
    "nfl": (22.0, 1.5, 3.5),
    "ncaaf": (27.0, 2.0, 7.0),
    "nba": (113.0, 1.5, 3.5),
    "ncaab": (71.0, 1.8, 5.0),
    "mlb": (math.log(4.4), 0.03, 0.10),
    "nhl": (math.log(3.0), 0.04, 0.10),
    "soccer": (math.log(1.35), 0.18, 0.25),
}
_TEAMS = {"nfl": 32, "ncaaf": 80, "nba": 30, "ncaab": 100, "mlb": 30, "nhl": 32, "soccer": 20}
_GAMES_PER_TEAM = {"nfl": 17, "ncaaf": 12, "nba": 60, "ncaab": 28, "mlb": 120, "nhl": 60, "soccer": 30}
_EVENTS = {"nfl": 8, "ncaaf": 12, "nba": 8, "ncaab": 12, "mlb": 10, "nhl": 8, "soccer": 6}

# role -> stat key -> (low mean, high mean, kind)
_ROLES: dict[str, list[tuple[str, int, dict[str, tuple[float, float, str]]]]] = {
    "basketball": [
        ("G", 4, {"PTS": (8, 28, "normal"), "REB": (2.5, 5, "count"), "AST": (2, 8, "count"),
                  "3PT": (1, 3.5, "count"), "BLK": (0.2, 0.6, "count"), "STL": (0.6, 1.6, "count"),
                  "TO": (1, 3, "count")}),
        ("F", 4, {"PTS": (8, 24, "normal"), "REB": (5, 11, "count"), "AST": (1, 4, "count"),
                  "3PT": (0.3, 2, "count"), "BLK": (0.5, 2, "count"), "STL": (0.5, 1.2, "count"),
                  "TO": (1, 2.5, "count")}),
    ],
    "football": [
        ("QB", 1, {"passing.YDS": (190, 290, "normal"), "passing.TD": (1.1, 2.3, "count"),
                   "passing.C/ATT": (18, 26, "count"), "passing.C/ATT_A": (29, 38, "count"),
                   "passing.INT": (0.5, 1.0, "count"), "rushing.YDS": (5, 35, "normal"),
                   "rushing.CAR": (2, 6, "count")}),
        ("RB", 1, {"rushing.YDS": (45, 95, "normal"), "rushing.CAR": (11, 20, "count"),
                   "receiving.YDS": (10, 35, "normal"), "receiving.REC": (1.5, 4, "count")}),
        ("WR", 3, {"receiving.YDS": (30, 85, "normal"), "receiving.REC": (2.5, 7, "count")}),
    ],
    "mlb": [
        ("B", 9, {"batting.H": (0.7, 1.3, "count"), "batting.HR": (0.08, 0.3, "count"),
                  "batting.RBI": (0.3, 0.8, "count"), "batting.R": (0.35, 0.8, "count"),
                  "batting.BB": (0.2, 0.6, "count"), "batting.K": (0.6, 1.4, "count")}),
        ("SP", 1, {"pitching.K": (4, 8.5, "count"), "pitching.H": (4, 6.5, "count"),
                   "pitching.BB": (1.3, 3, "count"), "pitching.ER": (1.8, 3.4, "count"),
                   "pitching.IP_OUTS": (15, 19, "count")}),
    ],
    "nhl": [
        ("F", 6, {"G": (0.12, 0.45, "count"), "A": (0.2, 0.6, "count"), "S": (1.5, 3.8, "count"),
                  "BS": (0.3, 0.9, "count")}),
        ("D", 3, {"G": (0.04, 0.15, "count"), "A": (0.2, 0.5, "count"), "S": (1.2, 2.5, "count"),
                  "BS": (1.2, 2.4, "count")}),
        ("GK", 1, {"SV": (24, 30, "normal")}),
    ],
}

_BOOKS = ("draftkings", "fanduel", "betmgm", "caesars", "pinnacle")
_FIRST = ["James", "Marcus", "Tyler", "Andre", "Luis", "Kevin", "Jalen", "Chris", "Devin", "Mason",
          "Ryan", "Isaiah", "Noah", "Tre", "Mikal", "Darius", "Josh", "Cole", "Evan", "Zach"]
_LAST = ["Walker", "Brooks", "Hayes", "Reed", "Carter", "Price", "Bennett", "Coleman", "Foster", "Hughes",
         "Ellis", "Graham", "Porter", "Simmons", "Wallace", "Jordan", "Fisher", "Stone", "Warren", "Bishop"]
_CITIES = ["Atlanta", "Boston", "Chicago", "Dallas", "Denver", "Houston", "Miami", "Phoenix", "Portland",
           "Seattle", "Austin", "Memphis", "Omaha", "Raleigh", "Tampa", "Tulsa", "Reno", "Boise", "Fresno",
           "Toledo", "Albany", "Mobile", "Eugene", "Provo", "Laredo", "Akron", "Dayton", "Spokane",
           "Madison", "Lincoln", "Wichita", "Tucson"]
_NICKS = ["Hawks", "Wolves", "Bears", "Comets", "Pilots", "Rangers", "Storm", "Knights", "Foxes", "Rams",
          "Owls", "Bison", "Lynx", "Vipers", "Mariners", "Titans"]


def _family(cfg: SportConfig) -> str:
    return "soccer" if cfg.draws else cfg.key


def _role_family(cfg: SportConfig) -> str | None:
    fam = _family(cfg)
    return {"nba": "basketball", "ncaab": "basketball", "nfl": "football", "ncaaf": "football",
            "mlb": "mlb", "nhl": "nhl"}.get(fam)


def _seed(*parts) -> int:
    return int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:12], 16)


class SampleProvider:
    def __init__(self, seed: int = 7, now: datetime | None = None, book_noise: float = 0.3):
        self.seed = seed
        self.now = now or datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        self.book_noise = book_noise  # book rating error, as a fraction of the team spread
        self.clock = self.now  # "current time" for events/odds; move it to replay past days
        self._worlds: dict[str, dict] = {}

    # --------------------------------------------------------------- world gen
    def _world(self, cfg: SportConfig) -> dict:
        if cfg.key in self._worlds:
            return self._worlds[cfg.key]
        rng = random.Random(_seed(self.seed, cfg.key))
        fam = _family(cfg)
        base, hfa, spread = _SCORING[fam]
        n = _TEAMS[fam]
        names: list[str] = []
        while len(names) < n:
            name = f"{rng.choice(_CITIES)} {rng.choice(_NICKS)}"
            if name not in names:
                names.append(name)
        truth = TeamRatings(
            link=cfg.link, mu=base, hfa=hfa,
            off={t: rng.gauss(0, spread) for t in names},
            deff={t: rng.gauss(0, spread) for t in names},
        )
        noise = spread * self.book_noise
        book_view = TeamRatings(
            link=cfg.link, mu=base, hfa=hfa,
            off={t: v + rng.gauss(0, noise) for t, v in truth.off.items()},
            deff={t: v + rng.gauss(0, noise) for t, v in truth.deff.items()},
        )

        # Past results
        results: list[GameResult] = []
        n_games = n * _GAMES_PER_TEAM[fam] // 2
        span = 170 if fam != "nfl" else 120
        for i in range(n_games):
            h, a = rng.sample(names, 2)
            when = self.now - timedelta(days=span * (1 - i / n_games) + 0.5, hours=rng.randint(0, 6))
            hs, as_ = self._simulate(cfg, truth, h, a, rng)
            results.append(GameResult(f"{cfg.key}-g{i}", when, h, a, hs, as_))

        # Upcoming slate (each team at most once)
        order = names[:]
        rng.shuffle(order)
        for i in range(min(_EVENTS[fam], n // 2)):
            h, a = order[2 * i], order[2 * i + 1]
            start = self.now + timedelta(hours=3 + i)
            results.append(GameResult(f"{cfg.key}-e{i}", start, h, a, None, None, state="pre"))

        # Rosters
        players: dict[str, list[tuple[str, dict[str, tuple[float, str]]]]] = {}
        fam_roles = _role_family(cfg)
        used: set[str] = set()
        for team in names:
            roster = []
            for _role, count, stats in _ROLES.get(fam_roles, []):
                for _ in range(count):
                    pname = f"{rng.choice(_FIRST)} {rng.choice(_LAST)}"
                    while pname in used:
                        pname = f"{rng.choice(_FIRST)} {chr(65 + rng.randrange(26))}. {rng.choice(_LAST)}"
                    used.add(pname)
                    roster.append((pname, {k: (rng.uniform(lo, hi), kind) for k, (lo, hi, kind) in stats.items()}))
            players[team] = roster

        out_players = {
            team: {p for p, _ in roster if rng.random() < 0.04} for team, roster in players.items()
        }
        world = {"truth": truth, "book": book_view, "results": results, "players": players, "out": out_players}
        self._worlds[cfg.key] = world
        return world

    def _simulate(self, cfg, ratings: TeamRatings, h: str, a: str, rng: random.Random) -> tuple[float, float]:
        eh, ea = ratings.expected_scores(h, a)
        if cfg.scoring == "normal":
            sd = cfg.margin_sd / math.sqrt(2)
            return max(0, round(rng.gauss(eh, sd))), max(0, round(rng.gauss(ea, sd)))
        hs, as_ = _draw_count(rng, eh, cfg.dispersion), _draw_count(rng, ea, cfg.dispersion)
        if hs == as_ and not cfg.draws:  # overtime / extra innings
            if rng.random() < eh / (eh + ea):
                hs += 1
            else:
                as_ += 1
        return hs, as_

    def _game_quotes(self, cfg: SportConfig, world: dict, ev: Event) -> list[Quote]:
        """Opening lines come from the books' noisy ratings; prices then drift
        toward the true odds as kick-off approaches (sharp money moving them)."""
        rng = random.Random(_seed(self.seed, ev.id, "lines"))
        pred = predict_game(cfg, world["book"], ev.home, ev.away)
        true = predict_game(cfg, world["truth"], ev.home, ev.away)
        hours = (ev.commence_time - self.clock).total_seconds() / 3600
        drift = min(max(1 - hours / 48, 0.0), 1.0) * 0.6
        quotes: list[Quote] = []
        if cfg.scoring == "normal":
            spread_line = -round(pred.exp_margin * 2) / 2
            total_line = round(pred.exp_total * 2) / 2
        else:
            spread_line = -1.5 if pred.exp_margin > 0 else 1.5
            total_line = round(pred.exp_total * 2) / 2
            if total_line == int(total_line):
                total_line += 0.5
        for book in _BOOKS:
            vig = 0.025 if book == "pinnacle" else 0.045
            jitter = rng.gauss(0, 0.012)
            sides = ["home", "away"] + (["draw"] if cfg.draws else [])
            probs = [_mix(pred.moneyline(s)[0], true.moneyline(s)[0], drift) for s in sides]
            probs[0] += jitter
            probs[1] -= jitter
            quotes += [Quote("h2h", s, None, book, _price(p, vig, len(sides))) for s, p in zip(sides, probs)]
            if not cfg.draws:
                p = _mix(_cond(pred.spread("home", spread_line)), _cond(true.spread("home", spread_line)), drift)
                p += rng.gauss(0, 0.01)
                quotes.append(Quote("spreads", "home", spread_line, book, _price(p, vig)))
                quotes.append(Quote("spreads", "away", -spread_line, book, _price(1 - p, vig)))
            p = _mix(_cond(pred.total("over", total_line)), _cond(true.total("over", total_line)), drift)
            p += rng.gauss(0, 0.01)
            quotes.append(Quote("totals", "over", total_line, book, _price(p, vig)))
            quotes.append(Quote("totals", "under", total_line, book, _price(1 - p, vig)))
        return quotes

    # ------------------------------------------------------------ provider API
    def game_results(self, cfg: SportConfig, start: datetime, end: datetime) -> list[GameResult]:
        return [g for g in self._world(cfg)["results"] if start <= g.date <= end]

    def events(self, cfg: SportConfig, horizon_hours: float = 36) -> list[Event]:
        """Games starting within ``horizon_hours`` of the clock, priced as of the clock."""
        world = self._world(cfg)
        out = []
        for g in world["results"]:
            hours = (g.date - self.clock).total_seconds() / 3600
            if 0 < hours <= horizon_hours:
                ev = Event(id=g.event_id, sport=cfg.key, commence_time=g.date, home=g.home, away=g.away)
                ev.quotes = self._game_quotes(cfg, world, ev)
                out.append(ev)
        return out

    def live_games(self, cfg: SportConfig) -> list[GameResult]:
        return []  # the demo has no games in progress

    def injuries(self, cfg: SportConfig) -> dict[str, list[tuple[str, str]]]:
        return {team: [(p, "Out") for p in sorted(names)] for team, names in self._world(cfg)["out"].items() if names}

    def box_score(self, cfg: SportConfig, game: GameResult) -> list[PlayerGame]:
        world = self._world(cfg)
        out = []
        for team in (game.home, game.away):
            for pname, stats in world["players"].get(team, []):
                line = self._player_line(game.event_id, pname, stats)
                if line is not None:
                    out.append(PlayerGame(game.event_id, game.date, pname, team, line))
        return out

    def _player_line(self, event_id: str, pname: str, stats: dict) -> dict[str, float] | None:
        rng = random.Random(_seed(self.seed, event_id, pname))
        if rng.random() < 0.05:
            return None  # rest day / injury
        line = {}
        for key, (mean, kind) in stats.items():
            if kind == "normal":
                line[key] = max(0.0, round(rng.gauss(mean, 0.4 * mean)))
            else:
                line[key] = float(_draw_count(rng, mean, _prop_dispersion(mean)))
        return line

    def prop_quotes(self, cfg: SportConfig, event: Event, markets: list[str]) -> list[Quote]:
        world = self._world(cfg)
        rng = random.Random(_seed(self.seed, event.id, "props"))
        quotes: list[Quote] = []
        for team in (event.home, event.away):
            for pname, stats in world["players"].get(team, []):
                if pname in world["out"].get(team, ()):
                    continue  # books pull props for players ruled out
                for market in markets:
                    spec = cfg.props.get(market)
                    if spec is None:
                        continue
                    keys = [c[0] for c in spec.terms]
                    if not all(k in stats for k in keys):
                        continue
                    mean = sum(stats[k][0] for k in keys)
                    kind = "normal" if any(stats[k][1] == "normal" for k in keys) else "count"
                    book_mean = mean * (1 + rng.gauss(0, 0.08))
                    pmf = _stat_pmf(book_mean, kind)
                    line = math.floor(book_mean) + 0.5 if kind == "count" else round(book_mean) + 0.5
                    win, _, loss = line_probs(pmf, -line)
                    p_over = win / (win + loss)
                    if not 0.08 < p_over < 0.92:
                        continue
                    for book in _BOOKS[:4]:
                        j = rng.gauss(0, 0.015)
                        quotes.append(Quote(market, "over", line, book, _price(p_over + j, 0.05), pname))
                        quotes.append(Quote(market, "under", line, book, _price(1 - p_over - j, 0.05), pname))
        return quotes

    def player_games(self, cfg: SportConfig, results: list[GameResult], team: str) -> list[PlayerGame]:
        world = self._world(cfg)
        games = [g for g in world["results"] if g.completed and team in (g.home, g.away) and g.date < self.clock]
        games = sorted(games, key=lambda g: g.date)[-cfg.prop_games:]
        out = []
        for g in games:
            out.extend(self.box_score(cfg, g))
        return out


def _mix(a: float, b: float, w: float) -> float:
    return a + (b - a) * w


def _cond(outcome: tuple[float, float, float]) -> float:
    win, _, loss = outcome
    return win / (win + loss) if win + loss > 0 else 0.5


def _stat_pmf(mean: float, kind: str):
    if kind == "normal":
        return discrete_normal_pmf(mean, 0.4 * mean, lo=0)
    return count_pmf(mean, mean + mean**2 / _prop_dispersion(mean))


def _prop_dispersion(mean: float) -> float:
    # Mild overdispersion: variance ~ 1.3x the mean for typical counting stats.
    return max(3.0, mean / 0.3)


def _draw_count(rng: random.Random, mean: float, dispersion: float | None) -> int:
    lam = mean
    if dispersion:
        lam = rng.gammavariate(dispersion, mean / dispersion)
    # Knuth Poisson sampler (means here are small)
    if lam > 30:
        return max(0, round(rng.gauss(lam, math.sqrt(lam))))
    L, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= L:
            return k
        k += 1


def _price(p: float, vig: float, n_sides: int = 2) -> float:
    p = min(max(p, 0.02), 0.98)
    implied = p * (1 + vig)
    implied = min(implied, 0.985)
    return decimal_to_american(1 / implied)
